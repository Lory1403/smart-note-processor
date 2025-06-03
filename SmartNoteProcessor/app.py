import os
import logging
import uuid
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, make_response
from werkzeug.utils import secure_filename
import tempfile
import shutil
import io
import zipfile
import re # Import regular expression module
import math
import hashlib
from moviepy.editor import VideoFileClip

from utils.document_processor import DocumentProcessor
from utils.openrouter_client import OpenRouterClient
from utils.topic_extractor import TopicExtractor
from utils.format_converter import FormatConverter
from utils.image_analyzer import ImageAnalyzer
from utils.resumes_enhancer import ResumeesEnhancer # Import ResumeesEnhancer
from database import db
from models import db, Document, Topic, Note, ChatMessage # Aggiungi ChatMessage
from orchestrator import SmartNotesOrchestrator
from datetime import datetime # Assicurati che datetime sia importato

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")

# --- Add Persistent Upload Folder Configuration ---
app.config['UPLOAD_FOLDER'] = os.path.join(app.instance_path, 'uploads')
# Ensure the base upload folder exists
try:
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    logger.info(f"Upload folder set to: {app.config['UPLOAD_FOLDER']}")
except OSError as e:
    logger.error(f"Could not create upload folder at {app.config['UPLOAD_FOLDER']}: {e}")
# --- End Configuration ---

# Configure database
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///smartnotes.db")
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Initialize database with app
db.init_app(app)

# Import models after db is initialized to avoid circular imports
# from models import Document, Topic, Note, ImageAnalysis # This line is usually here

# Initialize components
openrouter_client = OpenRouterClient()
format_converter = FormatConverter()
topic_extractor = TopicExtractor(openrouter_client)
image_analyzer = ImageAnalyzer(openrouter_client)
document_processor = DocumentProcessor(topic_extractor)
resumes_enhancer = ResumeesEnhancer(openrouter_client) # Initialize ResumeesEnhancer

notes_orchestrator = SmartNotesOrchestrator(
    document_processor=document_processor,
    topic_extractor=topic_extractor,
    openrouter_client=openrouter_client,
    format_converter=format_converter,
    image_analyzer=image_analyzer,
    resumes_enhancer=resumes_enhancer, # Pass resumes_enhancer
    db=db,
    app_config=app.config,
    flask_app=app  # Pass the Flask app instance
)

# In-memory storage for user sessions (will gradually be replaced by DB)
# Structure: { session_id: { 'document_content': str, 'topics': {}, 'current_granularity': int } }
sessions_data = {}

# Allowed file extensions
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'docx', 'md', 'mp4', 'mov', 'avi', 'mkv', 'mp3', 'wav', 'm4a', 'aac', 'ogg', 'flac'} # Added video and audio formats

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    # Create a unique session ID if not exists
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
        sessions_data[session['session_id']] = {
            'document_content': '',
            'topics': {},
            'current_granularity': 50
        }
    
    # Get all documents from database for display
    documents = Document.query.order_by(Document.created_at.desc()).all()
    
    return render_template('index.html', documents=documents)


@app.route('/load_document/<int:document_id>')
def load_document(document_id):
    # Load a previously saved document from the database
    try:
        # Get document from database
        document = Document.query.get_or_404(document_id)
        
        # Get topics for this document
        topics = Topic.query.filter_by(document_id=document.id).all()
        
        # Create topics dictionary
        topics_dict = {}
        for topic in topics:
            topics_dict[topic.topic_id] = {
                'name': topic.name,
                'description': topic.description or ''
            }
        
        # Create session ID if not exists
        if 'session_id' not in session:
            session['session_id'] = str(uuid.uuid4())
        else:
            # If switching documents, create a new session
            session['session_id'] = str(uuid.uuid4())
        
        # Setup session data
        sessions_data[session['session_id']] = {
            'document_content': document.content,
            'topics': topics_dict,
            'current_granularity': 50,  # Default granularity
            'document_id': document.id
        }
        
        # Get previously generated notes for display
        notes_data = {}
        for topic in topics:
            # Get notes for this topic
            notes = Note.query.filter_by(topic_id=topic.id).all()
            if notes:
                for note in notes:
                    if topic.topic_id not in notes_data:
                        notes_data[topic.topic_id] = {
                            'name': topic.name,
                            'content': note.content,
                            'format': note.format
                        }
        
        # Store notes in session if available
        if notes_data:
            sessions_data[session['session_id']]['generated_notes'] = notes_data
        
        flash('Document loaded successfully!', 'success')
        return redirect(url_for('results'))
    except Exception as e:
        logger.error(f"Error loading document: {str(e)}")
        flash(f'Error loading document: {str(e)}', 'danger')
        return redirect(url_for('index'))

@app.route('/upload', methods=['POST'])
def upload_file():
    uploaded_files = request.files.getlist('file')
    youtube_link = request.form.get('youtube_link', '').strip()

    # Scarica il video YouTube se presente
    if youtube_link:
        import yt_dlp
        temp_dir = tempfile.mkdtemp()
        ydl_opts = {
            'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'merge_output_format': 'mp4',
            'quiet': True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(youtube_link, download=True)
                video_path = ydl.prepare_filename(info)
                # Se il file non è mp4, rinominalo
                if not video_path.endswith('.mp4'):
                    new_video_path = os.path.splitext(video_path)[0] + '.mp4'
                    os.rename(video_path, new_video_path)
                    video_path = new_video_path
                # Simula un file caricato
                class FileObj:
                    def __init__(self, path):
                        self.filename = os.path.basename(path)
                        self.path = path
                    def save(self, dst):
                        shutil.copy2(self.path, dst)
                uploaded_files = list(uploaded_files) + [FileObj(video_path)]
                logger.info(f"Scaricato video YouTube: {video_path}")
        except Exception as e:
            logger.error(f"Errore nel download del video YouTube: {e}")
            flash(f"Errore nel download del video YouTube: {e}", "danger")

    if not uploaded_files or all(f.filename == '' for f in uploaded_files):
        flash('No selected file(s)', 'danger')
        return redirect(url_for('index'))

    # Ensure session exists
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
        sessions_data[session['session_id']] = {
            'document_content': '',
            'topics': {},
            'current_granularity': 50,
            'processed_document_ids': [],
            'document_id': None # Will store the primary document ID
        }
    session_id = session['session_id']
    # Clear previous session data for a new upload batch
    sessions_data[session_id] = {
        'document_content': '',
        'topics': {},
        'current_granularity': 50,
        'processed_document_ids': [],
        'document_id': None
    }


    processed_count = 0
    error_count = 0
    combined_content = ""
    processed_document_ids = []
    document_upload_folder = None
    images_folder = None

    try:
        # --- Process files first to get IDs ---
        temp_files_to_move = {} # Store temporary paths before moving to persistent folder

        for file in uploaded_files:
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                # Save to a temporary location first
                temp_dir = tempfile.mkdtemp()
                temp_file_path = os.path.join(temp_dir, filename)
                file.save(temp_file_path)
                logger.info(f"Temporarily saved file: {temp_file_path}")

                try:
                    # 1. Extract text
                    current_content = document_processor.extract_text(temp_file_path, filename)
                    combined_content += f"\n\n--- START DOCUMENT: {filename} ---\n\n" + current_content + f"\n\n--- END DOCUMENT: {filename} ---\n\n"

                    # 3. Store individual document in database (commit later)
                    title = filename.rsplit('.', 1)[0]
                    file_type = filename.rsplit('.', 1)[1].lower()
                    document = Document(
                        title=title,
                        content=current_content, # Store extracted text
                        filename=filename,
                        file_type=file_type
                    )
                    db.session.add(document)
                    db.session.flush() # Get ID
                    doc_id = document.id
                    processed_document_ids.append(doc_id)
                    logger.info(f"Prepared Document record for {filename} with ID {doc_id}")

                    # Store temp path associated with doc_id for moving later
                    temp_files_to_move[doc_id] = {'temp_path': temp_file_path, 'filename': filename, 'temp_dir': temp_dir}

                    processed_count += 1

                except Exception as e:
                    db.session.rollback()
                    logger.error(f"Error processing file {filename}: {str(e)}", exc_info=True)
                    flash(f'Error processing file {filename}: {str(e)}', 'danger')
                    error_count += 1
                    # Clean up temporary file/dir for this failed file
                    try:
                        shutil.rmtree(temp_dir)
                    except Exception as cleanup_err:
                         logger.error(f"Error removing temp dir {temp_dir}: {cleanup_err}")
            elif file:
                 flash(f'Invalid file type for {file.filename}. Allowed types: {", ".join(ALLOWED_EXTENSIONS)}', 'danger')
                 error_count += 1

        # --- After trying to process all files ---
        if processed_count > 0:
            # Get the primary document ID (first successfully processed)
            primary_document_id = processed_document_ids[0]

            # --- Create Persistent Folders ---
            document_upload_folder = os.path.join(app.config['UPLOAD_FOLDER'], str(primary_document_id))
            images_folder = os.path.join(document_upload_folder, "images")
            try:
                os.makedirs(document_upload_folder, exist_ok=True)
                os.makedirs(images_folder, exist_ok=True)
                logger.info(f"Ensured persistent folders exist: {document_upload_folder}, {images_folder}")
            except OSError as e:
                 logger.error(f"Could not create persistent folders for doc {primary_document_id}: {e}")
                 flash(f'Error creating storage folders for document.', 'danger')
                 # Rollback and redirect if folders can't be created
                 db.session.rollback()
                 # Clean up any remaining temp dirs
                 for data in temp_files_to_move.values():
                     try: shutil.rmtree(data['temp_dir'])
                     except: pass
                 return redirect(url_for('index'))

            # --- Move files and Extract Images/Frames to Persistent Folders ---
            moved_files_paths = {}
            for doc_id, data in temp_files_to_move.items():
                persistent_file_path = os.path.join(document_upload_folder, data['filename'])
                try:
                    shutil.move(data['temp_path'], persistent_file_path)
                    moved_files_paths[doc_id] = persistent_file_path
                    logger.info(f"Moved {data['filename']} to {persistent_file_path}")

                    file_ext_lower = data['filename'].lower()

                    # 2a. Extract images (if PDF)
                    if file_ext_lower.endswith('.pdf'):
                        try:
                            import fitz  # PyMuPDF
                            os.makedirs(images_folder, exist_ok=True)  # Crea cartella se manca
                            pdf_document = fitz.open(persistent_file_path)
                            image_count = 0
                            logger.info(f"Processing PDF {data['filename']} with {len(pdf_document)} pages")

                            for page_num in range(len(pdf_document)):
                                page = pdf_document[page_num]
                                image_list = page.get_images(full=True)
                                
                                if not image_list:
                                    logger.debug(f"No images found in page {page_num+1}")
                                    continue

                                for img_index, img in enumerate(image_list):
                                    xref = img[0]
                                    base_image = pdf_document.extract_image(xref)
                                    image_bytes = base_image["image"]
                                    image_ext = base_image["ext"]
                                    
                                    # Genera un nome file univoco con hash
                                    image_hash = hashlib.md5(image_bytes).hexdigest()[:8]
                                    image_filename = f"{os.path.splitext(data['filename'])[0]}_page{page_num+1}_img{image_hash}.{image_ext}"
                                    
                                    # Salva l'immagine
                                    with open(os.path.join(images_folder, image_filename), "wb") as img_file:
                                        img_file.write(image_bytes)
                                        image_count += 1
                                        logger.debug(f"Extracted image {image_filename} (size: {len(image_bytes)} bytes)")

                            logger.info(f"Extracted {image_count} images from {data['filename']}")
                        except ImportError:
                            logger.warning("PyMuPDF (fitz) not installed. PDF image extraction skipped.")
                        except Exception as e:
                            logger.error(f"Failed to extract images: {str(e)}", exc_info=True)
                        finally:
                            if 'pdf_document' in locals():
                                pdf_document.close()

                    # 2b. Extract frames (if Video)
                    elif file_ext_lower.endswith(('.mp4', '.mov', '.avi', '.mkv')):
                        if VideoFileClip:
                            try:
                                logger.info(f"Extracting frames from video: {persistent_file_path}")
                                video = VideoFileClip(persistent_file_path)
                                duration = video.duration # Duration in seconds
                                frame_count = 0
                                # Extract frame every N seconds (e.g., 10 seconds)
                                interval = 10
                                for t in range(0, math.ceil(duration), interval):
                                    frame_filename = f"{os.path.splitext(data['filename'])[0]}_frame_at_{t}s.jpg"
                                    frame_path = os.path.join(images_folder, frame_filename)
                                    try:
                                        video.save_frame(frame_path, t=t)
                                        frame_count += 1
                                    except Exception as frame_save_err:
                                         logger.error(f"Error saving frame at {t}s for {data['filename']}: {frame_save_err}")
                                video.close() # Close video file handle
                                logger.info(f"Extracted {frame_count} frames from {data['filename']} into {images_folder}")
                            except Exception as video_err:
                                logger.error(f"Error extracting frames from video {data['filename']}: {str(video_err)}")
                        else:
                            logger.warning(f"Frame extraction skipped for {data['filename']}: moviepy library not available.")

                except Exception as move_err:
                    logger.error(f"Error moving file {data['filename']} to persistent storage: {move_err}")
                    # Handle error - maybe remove the DB record? For now, just log.
                finally:
                    # Clean up the temporary directory regardless of move success/failure
                    try:
                        shutil.rmtree(data['temp_dir'])
                    except Exception as cleanup_err:
                        logger.error(f"Error removing temp dir {data['temp_dir']} after processing: {cleanup_err}")


            # --- Extract Text for Video and Audio Files (Now that they are in persistent storage) ---
            temp_combined_content = ""
            # Assuming processed_document_ids is a list of IDs for documents processed in this upload
            # And moved_files_paths is a dict mapping doc.id to its persistent path
            for doc_id in processed_document_ids: # Make sure processed_document_ids and moved_files_paths are correctly populated
                doc = Document.query.get(doc_id)
                if doc:
                    file_ext_lower = os.path.splitext(doc.filename)[1].lower()
                    
                    # Check if it's a video or audio file
                    if file_ext_lower in ALLOWED_EXTENSIONS or file_ext_lower in ALLOWED_EXTENSIONS:
                        persistent_file_path = moved_files_paths.get(doc.id) 
                        
                        if persistent_file_path and os.path.exists(persistent_file_path):
                            try:
                                operation_type = "Audio Transcription" if file_ext_lower in ALLOWED_EXTENSIONS else "Video Transcription"
                                logger.info(f"Starting {operation_type} for: {doc.filename} from {persistent_file_path}")
                                
                                # Use the updated document_processor.extract_text method
                                extracted_text = document_processor.extract_text(persistent_file_path, doc.filename)
                                
                                doc.content = extracted_text # Update document content in DB
                                db.session.add(doc) # Stage update for commit later
                                
                                temp_combined_content += f"\n\n--- START DOCUMENT: {doc.filename} ({operation_type}) ---\n\n" + extracted_text + f"\n\n--- END DOCUMENT: {doc.filename} ---\n\n"
                                logger.info(f"{operation_type} added for {doc.filename}")
                                
                            except Exception as trans_err:
                                error_type = "audio transcription" if file_ext_lower in ALLOWED_EXTENSIONS else "video transcription"
                                logger.error(f"Failed {error_type} for {doc.filename}: {trans_err}", exc_info=True)
                                # Keep original placeholder content or mark as failed
                                original_content_for_failure = doc.content if doc.content and not doc.content.startswith("Placeholder content") else "Extraction failed."
                                temp_combined_content += f"\n\n--- START DOCUMENT: {doc.filename} ({operation_type} FAILED) ---\n\n" + original_content_for_failure + f"\n\n--- END DOCUMENT: {doc.filename} ---\n\n"
                        else:
                            missing_type = "Audio" if file_ext_lower in ALLOWED_EXTENSIONS else "Video"
                            logger.warning(f"Persistent path not found or file does not exist for {missing_type.lower()} {doc.filename} (expected at {persistent_file_path}), skipping transcription.")
                            original_content_for_missing = doc.content if doc.content and not doc.content.startswith("Placeholder content") else "Path missing or file inaccessible."
                            temp_combined_content += f"\n\n--- START DOCUMENT: {doc.filename} ({missing_type} - Path Missing or File Inaccessible) ---\n\n" + original_content_for_missing + f"\n\n--- END DOCUMENT: {doc.filename} ---\n\n"
                    elif file_ext_lower in ALLOWED_EXTENSIONS:
                        # How image "content" is added to combined_content depends on your strategy.
                        # Often, images are analyzed separately, not directly part of text to be topic-modelled.
                        # Using the placeholder from DocumentProcessor or a specific marker:
                        temp_combined_content += f"\n\n--- START DOCUMENT: {doc.filename} (Image File) ---\n\n" + (doc.content if doc.content else f"Image file: {doc.filename}. Content analyzed separately.") + f"\n\n--- END DOCUMENT: {doc.filename} ---\n\n"
                    else: # For text files (PDF, TXT, DOCX) whose content was extracted earlier
                        temp_combined_content += f"\n\n--- START DOCUMENT: {doc.filename} ---\n\n" + doc.content + f"\n\n--- END DOCUMENT: {doc.filename} ---\n\n"
            
            # Ensure changes to doc.content (transcriptions) are committed to the database
            try:
                db.session.commit()
                logger.info("Committed updated document contents (transcriptions) to database.")
            except Exception as e:
                db.session.rollback()
                logger.error(f"Error committing transcribed content to DB: {e}", exc_info=True)
                flash("Error saving transcriptions to the database.", "danger")
                # Handle redirect or error display as appropriate

            combined_content = temp_combined_content 
            sessions_data['document_content'] = combined_content

            # 4. Extract topics from combined content (including transcriptions)
            granularity = 50 # Default granularity
            logger.info(f"Extracting topics from combined content ({len(combined_content)} chars) at granularity {granularity}")
            topics_dict = topic_extractor.extract_topics(combined_content, granularity)

            # 5. Save Topics to DB (linked to the primary document ID)
            logger.info(f"Saving {len(topics_dict)} topics to DB, linked to primary document ID {primary_document_id}")
            for topic_id, topic_data in topics_dict.items():
                existing_topic = Topic.query.filter_by(document_id=primary_document_id, topic_id=topic_id).first()
                if not existing_topic:
                    topic = Topic(
                        topic_id=topic_id,
                        name=topic_data['name'],
                        description=topic_data.get('description', ''),
                        document_id=primary_document_id
                    )
                    db.session.add(topic)
                else:
                    logger.debug(f"Topic {topic_id} already exists for document {primary_document_id}, skipping.")

            db.session.commit() # Commit all documents and topics together

            # 6. Update Session Data (Remove upload_folder)
            sessions_data[session_id]['document_content'] = combined_content
            sessions_data[session_id]['topics'] = topics_dict
            sessions_data[session_id]['current_granularity'] = granularity
            sessions_data[session_id]['document_id'] = primary_document_id # Store primary ID
            sessions_data[session_id]['processed_document_ids'] = processed_document_ids # Store all IDs

            flash(f'{processed_count} document(s) uploaded and processed successfully!', 'success')
            if error_count > 0:
                 flash(f'{error_count} file(s) could not be processed.', 'warning')

            return redirect(url_for('results'))
        else:
            # No files processed successfully
            db.session.rollback()
            flash('No documents were processed successfully.', 'danger')
            # Clean up any remaining temp dirs
            for data in temp_files_to_move.values():
                try: shutil.rmtree(data['temp_dir'])
                except: pass
            return redirect(url_for('index'))

    except Exception as global_err:
        db.session.rollback()
        logger.error(f"An unexpected error occurred during file upload: {str(global_err)}", exc_info=True)
        flash(f'An unexpected error occurred: {str(global_err)}', 'danger')
        # Clean up any remaining temp dirs
        for data in temp_files_to_move.values():
            try: shutil.rmtree(data['temp_dir'])
            except: pass
        # Clean up persistent folder if partially created
        if document_upload_folder and os.path.exists(document_upload_folder):
             try: shutil.rmtree(document_upload_folder)
             except Exception as cleanup_err: logger.error(f"Error cleaning up persistent folder {document_upload_folder} after global error: {cleanup_err}")
        return redirect(url_for('index'))


@app.route('/results')
def results():
    session_id = session.get('session_id')
    if not session_id or session_id not in sessions_data:
        flash('No active session found. Please upload a document.', 'warning')
        return redirect(url_for('index'))
    
    session_data = sessions_data[session_id]
    topics = session_data.get('topics', {})
    notes = session_data.get('generated_notes', {})
    granularity = session_data.get('current_granularity', 50)
    viewing_topic = None
    
    current_document_id = session_data.get('document_id') # Questo dovrebbe essere l'ID intero del documento

    # --- CARICA CRONOLOGIA CHAT DAL DATABASE ---
    if current_document_id:
        try:
            db_chat_messages = ChatMessage.query.filter_by(document_id=current_document_id).order_by(ChatMessage.timestamp.asc()).all()
            loaded_chat_history = []
            for msg in db_chat_messages:
                loaded_chat_history.append({
                    "sender": msg.sender,
                    "message": msg.message
                })
            session_data['chat_history'] = loaded_chat_history # Sovrascrivi la cronologia della sessione con quella del DB
            logger.info(f"Caricati {len(loaded_chat_history)} messaggi chat dal DB per il documento {current_document_id}.")
        except Exception as e:
            logger.error(f"Errore durante il caricamento della cronologia chat dal DB per il documento {current_document_id}: {e}", exc_info=True)
            # Mantieni la cronologia chat esistente nella sessione o inizializza a vuota
            if 'chat_history' not in session_data:
                 session_data['chat_history'] = []
    elif 'chat_history' not in session_data: # Se non c'è document_id, assicurati che esista almeno una lista vuota
        session_data['chat_history'] = []
    # --- FINE CARICAMENTO CRONOLOGIA CHAT ---


    if notes and not request.args.get('topic_id'):
        first_topic_id = next(iter(notes))
        if first_topic_id in notes: # Verifica aggiuntiva
            viewing_topic = notes[first_topic_id]
            viewing_topic['topic_id'] = first_topic_id

    elif request.args.get('topic_id'):
        topic_id_req = request.args.get('topic_id')
        if topic_id_req in notes: # Verifica aggiuntiva
            viewing_topic = notes[topic_id_req]
            viewing_topic['topic_id'] = topic_id_req
        elif topics and topic_id_req in topics: # Fallback se la nota non è generata ma il topic esiste
             # Potresti voler mostrare solo le info del topic se la nota non c'è
             pass


    return render_template(
        'results.html',
        topics=topics,
        granularity=granularity,
        notes=notes,
        session_data=session_data, # Assicurati che session_data sia passato al template
        viewing_topic=viewing_topic,
        # selected_format è gestito altrove o non necessario qui se non si rigenerano note
    )

@app.route('/update_granularity', methods=['POST'])
def update_granularity():
    try:
        session_id = session.get('session_id')
        granularity = int(request.form.get('granularity', 50))
        
        if session_id and session_id in sessions_data:
            document_content = sessions_data[session_id]['document_content']
            document_id = sessions_data[session_id].get('document_id')
            
            # Re-extract topics with new granularity
            topics_dict = topic_extractor.extract_topics(document_content, granularity)
            sessions_data[session_id]['topics'] = topics_dict
            sessions_data[session_id]['current_granularity'] = granularity
            
            # If this is a document from the database, update its topics
            if document_id:
                # First, get existing topics to update or remove
                existing_topics = Topic.query.filter_by(document_id=document_id).all()
                existing_topic_ids = {topic.topic_id: topic for topic in existing_topics}
                
                # Add new topics and update existing ones
                for topic_id, topic_data in topics_dict.items():
                    if topic_id in existing_topic_ids:
                        # Update existing topic
                        existing_topic = existing_topic_ids[topic_id]
                        existing_topic.name = topic_data['name']
                        existing_topic.description = topic_data.get('description', '')
                    else:
                        # Create new topic
                        new_topic = Topic(
                            topic_id=topic_id,
                            name=topic_data['name'],
                            description=topic_data.get('description', ''),
                            document_id=document_id
                        )
                        db.session.add(new_topic)
                
                # Remove topics that no longer exist
                for old_topic_id, old_topic in existing_topic_ids.items():
                    if old_topic_id not in topics_dict:
                        db.session.delete(old_topic)
                
                # Commit changes
                db.session.commit()
            
            return redirect(url_for('results'))
        else:
            flash('No active session found. Please upload a document.', 'warning')
            return redirect(url_for('index'))
    except Exception as e:
        logger.error(f"Error updating granularity: {str(e)}")
        flash(f'Error updating granularity: {str(e)}', 'danger')
        return redirect(url_for('results'))

@app.route('/generate_notes', methods=['POST'])
def generate_notes():
    try:
        session_id = session.get('session_id')
        output_format = request.form.get('format', 'markdown')
        process_images = request.form.get('process_images', 'false') == 'true'

        if not session_id or session_id not in sessions_data:
            flash('Nessuna sessione attiva trovata. Per favore carica un documento.', 'warning')
            return redirect(url_for('index'))

        session_data = sessions_data[session_id]
        topics_dict = session_data.get('topics', {})
        combined_content = session_data.get('document_content', '')
        primary_document_id = session_data.get('document_id')

        if not topics_dict:
            flash('Nessun argomento trovato. Per favore aggiusta la granularità e riprova.', 'warning')
            return render_template(
                'results.html',
                topics={},
                granularity=session_data.get('current_granularity', 50),
                notes={},
                selected_format=output_format,
                session_data=session_data
            )

        if not primary_document_id:
            flash('ID documento non trovato nella sessione. Impossibile procedere con la generazione delle note.', 'danger')
            return render_template(
                'results.html',
                topics=topics_dict,
                granularity=session_data.get('current_granularity', 50),
                notes={},
                selected_format=output_format,
                session_data=session_data
            )
        
        # Determine the path to the document's specific upload folder
        document_specific_upload_folder = None
        if primary_document_id:
            # Assumes UPLOAD_FOLDER is the base, and each document has a subfolder named by its ID
            # e.g., instance/uploads/<document_id>/
            doc_folder_path = os.path.join(app.config['UPLOAD_FOLDER'], str(primary_document_id))
            if os.path.isdir(doc_folder_path):
                document_specific_upload_folder = doc_folder_path
            else:
                logger.warning(f"Document-specific upload folder not found at {doc_folder_path} for document ID {primary_document_id}. Image processing might be affected.")

        # Call the orchestrator's main processing method
        generated_notes, errors_encountered, successfully_processed_count = notes_orchestrator.process_and_generate(
            primary_document_id=primary_document_id,
            combined_content=combined_content,
            topics_dict=topics_dict,
            output_format=output_format,
            process_images_flag=process_images,
            document_upload_folder_path=document_specific_upload_folder
        )

        # Update session with the results from the orchestrator
        session_data['generated_notes'] = generated_notes
        session_data['selected_format'] = output_format
        # sessions_data[session_id] = session_data # Re-assign if session_data was a copy

        logger.info(f"Generazione note via orchestrator completata. Note generate: {successfully_processed_count}, Errori: {len(errors_encountered)}")

        # Flash messages based on outcome
        if not errors_encountered and successfully_processed_count == len(topics_dict) and successfully_processed_count > 0:
            flash(f'Tutte le {successfully_processed_count} note sono state generate con successo!', 'success')
        elif successfully_processed_count > 0:
            flash(f'Generazione parzialmente completata: {successfully_processed_count}/{len(topics_dict)} note generate. Controlla i messaggi per eventuali errori.', 'warning')
            for error in errors_encountered:
                flash(error, 'danger')
        else:
            flash('Generazione fallita. Nessuna nota è stata generata.', 'danger')
            for error in errors_encountered:
                flash(error, 'danger')

        return render_template(
            'results.html',
            topics=topics_dict,
            granularity=session_data.get('current_granularity', 50),
            notes=generated_notes,
            selected_format=output_format,
            session_data=session_data
        )

    except Exception as e:
        db.session.rollback()
        logger.error(f"Errore generale in /generate_notes: {str(e)}", exc_info=True)
        flash(f'Errore imprevisto durante la generazione delle note: {str(e)}', 'danger')
        # Fallback rendering
        session_id_fallback = session.get('session_id')
        if session_id_fallback and session_id_fallback in sessions_data:
            s_data = sessions_data[session_id_fallback]
            return render_template(
                'results.html',
                topics=s_data.get('topics', {}),
                granularity=s_data.get('current_granularity', 50),
                notes=s_data.get('generated_notes', {}),
                selected_format=request.form.get('format', 'markdown'),
                session_data=s_data
            )
        return redirect(url_for('index'))

@app.route('/download/<topic_id>', methods=['GET'])
def download_topic(topic_id):
    session_id = session.get('session_id')
    if not session_id or session_id not in sessions_data:
        flash('No active session found. Please upload a document.', 'warning')
        return redirect(url_for('index'))
    
    session_data = sessions_data[session_id]
    generated_notes = session_data.get('generated_notes', {})
    
    if topic_id not in generated_notes:
        flash('Topic not found', 'danger')
        return redirect(url_for('results'))
    
    topic_data = generated_notes[topic_id]
    format_extension = {
        'markdown': '.md',
        'html': '.html',
        'latex': '.tex'
    }.get(topic_data['format'], '.txt')
    
    filename = f"{topic_data['name'].replace(' ', '_')}{format_extension}"
    content = topic_data['content']
    
    from flask import Response
    response = Response(
        content,
        mimetype={
            'markdown': 'text/markdown',
            'html': 'text/html',
            'latex': 'application/x-latex'
        }.get(topic_data['format'], 'text/plain')
    )
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response

@app.route('/download_all', methods=['GET'])
def download_all():
    session_id = session.get('session_id')
    if not session_id or session_id not in sessions_data:
        flash('No active session found. Please upload a document.', 'warning')
        return redirect(url_for('index'))

    session_data = sessions_data[session_id]
    generated_notes = session_data.get('generated_notes', {})
    document_id = session_data.get('document_id')

    if not generated_notes:
        flash('No notes generated yet', 'danger')
        return redirect(url_for('results'))

    images_folder = None
    if document_id:
        document_upload_folder = os.path.join(app.config['UPLOAD_FOLDER'], str(document_id))
        images_folder = os.path.join(document_upload_folder, 'images')
        if not os.path.exists(images_folder):
            images_folder = None

    memory_file = io.BytesIO()
    added_images = set()

    with zipfile.ZipFile(memory_file, 'w') as zf:
        for topic_id, topic_data in generated_notes.items():
            format_extension = {
                'markdown': '.md',
                'html': '.html',
                'latex': '.tex'
            }.get(topic_data['format'], '.txt')

            note_filename = f"{topic_data['name'].replace(' ', '_')}{format_extension}"
            note_content = topic_data['content']

            if images_folder:
                # --- FIX: Change "Figure" to "Figura" ---
                image_references = re.findall(r"\b[\w\-/\\\.]+?\.(?:png|jpg|jpeg|gif|bmp)\b", note_content, re.IGNORECASE)
                # --- End Fix ---
                logger.debug(f"Found image references in note '{topic_data['name']}': {image_references}") # DEBUG


                for img_filename in image_references:
                    zip_image_path = os.path.join('images', img_filename)
                    if zip_image_path not in added_images:
                        if img_filename.startswith('images/'):
                            img_filename = img_filename[len('images/'):]
                        image_path = os.path.join(images_folder, img_filename)
                        logger.debug(f"Checking for image path: {image_path}") # DEBUG
                        logger.debug(f"Does image path exist? {os.path.exists(image_path)}") # DEBUG
                        if os.path.exists(image_path):
                            try:
                                zf.write(image_path, arcname=zip_image_path)
                                added_images.add(zip_image_path)
                                logger.info(f"Added image '{img_filename}' to zip.")

                                # Update note content with relative path
                                if topic_data['format'] == 'markdown':
                                     # --- FIX: Replace "Figura" ---
                                     note_content = note_content.replace(
                                         f"### Figura: {img_filename}",
                                         f"![{img_filename}](images/{img_filename})"
                                     )
                                elif topic_data['format'] == 'html':
                                     # --- FIX: Replace "Figura" ---
                                     note_content = note_content.replace(
                                         f"### Figura: {img_filename}",
                                         f'<p><img src="images/{img_filename}" alt="{img_filename}"></p>'
                                     )
                                # --- End Fixes ---

                            except Exception as zip_err:
                                logger.error(f"Failed to add image {img_filename} to zip: {zip_err}")
                        else:
                             logger.warning(f"Referenced image not found: {image_path}")

            # Write the (potentially modified) note content to the zip
            logger.debug(f"Final note content for '{note_filename}':\n{note_content[:200]}...") # DEBUG: Log start of content
            zf.writestr(note_filename, note_content)

    memory_file.seek(0)

    from flask import send_file
    return send_file(
        memory_file,
        mimetype='application/zip',
        as_attachment=True,
        download_name='smart_notes_with_images.zip'
    )

@app.route('/view/<topic_id>', methods=['GET'])
def view_topic(topic_id):
    session_id = session.get('session_id')
    if not session_id or session_id not in sessions_data:
        flash('No active session found. Please upload a document.', 'warning')
        return redirect(url_for('index'))
    
    session_data = sessions_data[session_id]
    generated_notes = session_data.get('generated_notes', {})
    
    if topic_id not in generated_notes:
        flash('Topic not found', 'danger')
        return redirect(url_for('results'))
    
    topic_data = generated_notes[topic_id]
    
    return render_template(
        'results.html',
        topics=session_data.get('topics', {}),
        granularity=session_data.get('current_granularity', 50),
        notes=generated_notes,
        selected_format=topic_data['format'],
        viewing_topic=topic_data,
        session_data=session_data
    )

@app.route('/delete_document/<int:document_id>', methods=['POST'])
def delete_document(document_id):
    try:
        document = Document.query.get_or_404(document_id)
        document_title = document.title

        document_upload_folder = os.path.join(app.config['UPLOAD_FOLDER'], str(document_id))
        if os.path.exists(document_upload_folder):
            try:
                shutil.rmtree(document_upload_folder)
                logger.info(f"Deleted persistent folder: {document_upload_folder}")
            except Exception as folder_del_err:
                logger.error(f"Error deleting persistent folder {document_upload_folder}: {folder_del_err}")

        db.session.delete(document)
        db.session.commit()

        session_id = session.get('session_id')
        if session_id and session_id in sessions_data:
            if sessions_data[session_id].get('document_id') == document_id:
                logger.info(f"Clearing session data for deleted document {document_id} in session {session_id}")
                sessions_data[session_id] = {
                    'document_content': '', 'topics': {}, 'current_granularity': 50,
                    'processed_document_ids': [], 'document_id': None
                }

        flash(f'Document "{document_title}" and associated files deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting document {document_id}: {str(e)}")
        flash(f'Error deleting document: {str(e)}', 'danger')

    return redirect(url_for('index'))


if __name__ == "__main__":
    try:
        os.makedirs(app.instance_path, exist_ok=True)
    except OSError as e:
         logger.error(f"Could not create instance folder at {app.instance_path}: {e}")
    app.run(host="0.0.0.0", port=5000, debug=True)


@app.route('/merge_topics', methods=['POST'])
def merge_topics():
    session_id = session.get('session_id')
    if not session_id or session_id not in sessions_data:
        flash('No active session found.', 'warning')
        return redirect(url_for('results'))

    selected_topic_ids = request.form.getlist('selected_topics')
    if len(selected_topic_ids) < 2:
        flash('Seleziona almeno due topic da unire.', 'warning')
        return redirect(url_for('results'))

    session_data = sessions_data[session_id]
    topics_dict = session_data.get('topics', {})
    combined_content = session_data.get('document_content', '')
    document_id = session_data.get('document_id')

    # Recupera i nomi e le descrizioni dei topic selezionati
    merged_topic_names = [topics_dict[tid]['name'] for tid in selected_topic_ids if tid in topics_dict]
    topic_titles = ", ".join(merged_topic_names)
    merged_topic_title = notes_orchestrator.create_unified_title(topic_titles)
    merged_description = "\n\n".join([topics_dict[tid]['description'] for tid in selected_topic_ids if tid in topics_dict])

    # Genera un nuovo topic_id unico
    new_topic_id = "merged_" + "_".join(selected_topic_ids)

    # --- DATABASE OPERATIONS ---
    from models import Topic, Note, db

    try:
        # 1. Crea il nuovo topic nel DB
        new_topic = Topic(
            topic_id=new_topic_id,
            name=merged_topic_title,
            description=merged_description,
            document_id=document_id
        )
        db.session.add(new_topic)
        db.session.flush()  # Per ottenere l'id del nuovo topic

        # 2. Elimina i vecchi topic e le relative note
        for tid in selected_topic_ids:
            old_topic = Topic.query.filter_by(topic_id=tid, document_id=document_id).first()
            if old_topic:
                # Elimina le note associate
                Note.query.filter_by(topic_id=old_topic.id).delete()
                db.session.delete(old_topic)

        db.session.commit()
        logger.info(f"Creato nuovo topic unito '{merged_topic_title}' e rimossi i vecchi topic dal DB.")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Errore durante il merge dei topic nel DB: {str(e)}")
        flash(f"Errore durante il merge dei topic nel database: {str(e)}", "danger")
        return redirect(url_for('results'))

    # --- SESSION UPDATE ---
    # Aggiorna il dizionario dei topics in sessione
    topics_dict[new_topic_id] = {
        'name': merged_topic_title,
        'description': merged_description
    }
    for tid in selected_topic_ids:
        topics_dict.pop(tid, None)
    session_data['topics'] = topics_dict

    flash(f'Creato nuovo topic unito: {merged_topic_title}', 'success')
    return redirect(url_for('results'))

@app.route('/summary_interaction', methods=['POST'])
def summary_interaction():
    user_instruction = request.form.get('user_instruction', '').strip()
    document_id_str = request.form.get('document_id')
    session_id = session.get('session_id')

    if not session_id or session_id not in sessions_data:
        # For AJAX, it's better to return an error JSON
        return jsonify({"error": "Sessione non trovata. Riprova.", "status": "error"}), 403

    session_data = sessions_data[session_id]
    topics_dict = session_data.get('topics', {})
    generated_notes = session_data.get('generated_notes', {})
    document_content = session_data.get('document_content', '')

    if 'chat_history' not in session_data:
        session_data['chat_history'] = []
    
    # This is the history up to the point BEFORE the current user message for the AI's context
    current_chat_history_for_orchestrator = list(session_data.get('chat_history', []))

    if not document_id_str:
        ai_error_message = "Error: Document ID is missing. Cannot process request."
        if user_instruction:
             session_data['chat_history'].append({"sender": "user", "message": user_instruction})
             session_data['chat_history'].append({"sender": "ai", "message": ai_error_message})
        return jsonify({"error": "ID Documento non trovato.", "ai_message": ai_error_message, "status": "error"}), 400

    try:
        doc_id_int = int(document_id_str)
    except ValueError:
        ai_error_message = "Error: Invalid Document ID."
        if user_instruction:
             session_data['chat_history'].append({"sender": "user", "message": user_instruction})
             session_data['chat_history'].append({"sender": "ai", "message": ai_error_message})
        return jsonify({"error": "ID Documento non valido.", "ai_message": ai_error_message, "status": "error"}), 400

    if not user_instruction:
        # For AJAX, we might not even hit this if client-side validation is good,
        # but if we do, return an appropriate JSON response.
        return jsonify({"error": "Per favore, inserisci una richiesta o una domanda.", "status": "info"}), 400

    # Add user message to session_data['chat_history'] for persistence
    # The client-side JS will add it to the view immediately.
    session_data['chat_history'].append({"sender": "user", "message": user_instruction})

    result_tuple = notes_orchestrator.apply_user_instruction(
        user_instruction,
        generated_notes,
        topics_dict,
        doc_id_int,
        document_content,
        current_chat_history_for_orchestrator 
    )

    updated_notes = result_tuple[0]
    status = result_tuple[1]
    ai_response_message = result_tuple[2] if len(result_tuple) > 2 else "An issue occurred processing your request."

    session_data['generated_notes'] = updated_notes

    # Add AI response to session_data['chat_history'] for persistence
    if ai_response_message: # Ensure there's a message to add
        session_data['chat_history'].append({"sender": "ai", "message": ai_response_message})

    # --- SALVA CHAT NEL DATABASE --- (This logic remains the same)
    try:
        user_chat_db = ChatMessage(
            document_id=doc_id_int,
            sender="user",
            message=user_instruction, 
            timestamp=datetime.utcnow() 
        )
        db.session.add(user_chat_db)

        if ai_response_message:
             ai_chat_db = ChatMessage(
                document_id=doc_id_int,
                sender="ai",
                message=ai_response_message, 
                timestamp=datetime.utcnow() 
            )
             db.session.add(ai_chat_db)
        
        db.session.commit()
        logger.info(f"Messaggi chat per il documento {doc_id_int} salvati nel DB.")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Errore durante il salvataggio dei messaggi chat nel DB per il documento {doc_id_int}: {e}", exc_info=True)
        # Return an error in JSON as well
        return jsonify({
            "user_message": user_instruction, # So client can display it if it hasn't already
            "ai_message": "Errore durante il salvataggio della cronologia chat nel database.",
            "status": "db_error"
        }), 500
    # --- FINE SALVATAGGIO CHAT NEL DATABASE ---

    # Limit chat history in session
    max_chat_history_items = 30 
    if len(session_data['chat_history']) > max_chat_history_items:
        session_data['chat_history'] = session_data['chat_history'][-max_chat_history_items:]

    # Return JSON instead of redirecting
    response_data = {
        "user_message": user_instruction, # Client might use this to confirm what was processed
        "ai_message": ai_response_message,
        "status": status
    }
    # Remove flash messages for normal chat operations
    # Flashes for critical errors can remain if they are handled outside this AJAX flow
    # or if you want to add them to the JSON response for the client to handle.

    return jsonify(response_data)