import os
import logging
import uuid
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.utils import secure_filename
import tempfile
import shutil
import json
from datetime import datetime
import io
import zipfile
import re # Import regular expression module

from utils.document_processor import DocumentProcessor
from utils.gemini_client import GeminiClient
from utils.topic_extractor import TopicExtractor
from utils.format_converter import FormatConverter
from utils.image_analyzer import ImageAnalyzer
from database import db

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
from models import Document, Topic, Note, ImageAnalysis

# Initialize components
gemini_client = GeminiClient(api_key=os.environ.get("OPENROUTER_API_KEY", ""))
topic_extractor = TopicExtractor(gemini_client)
document_processor = DocumentProcessor(topic_extractor)
format_converter = FormatConverter()
image_analyzer = ImageAnalyzer(gemini_client)

# In-memory storage for user sessions (will gradually be replaced by DB)
# Structure: { session_id: { 'document_content': str, 'topics': {}, 'current_granularity': int } }
sessions_data = {}

# Allowed file extensions
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'docx', 'md'}

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

            # --- Move files and Extract Images to Persistent Folders ---
            moved_files_paths = {}
            for doc_id, data in temp_files_to_move.items():
                persistent_file_path = os.path.join(document_upload_folder, data['filename'])
                try:
                    shutil.move(data['temp_path'], persistent_file_path)
                    moved_files_paths[doc_id] = persistent_file_path
                    logger.info(f"Moved {data['filename']} to {persistent_file_path}")

                    # 2. Extract images (if PDF) into the persistent images folder
                    if data['filename'].lower().endswith('.pdf'):
                        try:
                            import fitz  # PyMuPDF
                            pdf_document = fitz.open(persistent_file_path) # Open from new location
                            image_count = 0
                            for page_num in range(len(pdf_document)):
                                page = pdf_document[page_num]
                                image_list = page.get_images(full=True)
                                for img_index, img in enumerate(image_list):
                                    xref = img[0]
                                    base_image = pdf_document.extract_image(xref)
                                    image_bytes = base_image["image"]
                                    image_ext = base_image["ext"]
                                    image_filename = f"{os.path.splitext(data['filename'])[0]}_page{page_num+1}_img{img_index+1}.{image_ext}"
                                    # Save to persistent images folder
                                    with open(os.path.join(images_folder, image_filename), "wb") as img_file:
                                        img_file.write(image_bytes)
                                        image_count += 1
                            logger.info(f"Extracted {image_count} images from {data['filename']} into {images_folder}")
                        except ImportError as ie:
                            logger.warning(f"PDF image extraction skipped for {data['filename']}: {str(ie)}")
                        except Exception as pdf_err:
                            logger.error(f"Error extracting images from PDF {data['filename']}: {str(pdf_err)}")

                except Exception as move_err:
                    logger.error(f"Error moving file {data['filename']} to persistent storage: {move_err}")
                    # Handle error - maybe remove the DB record? For now, just log.
                finally:
                    # Clean up the temporary directory regardless of move success/failure
                    try:
                        shutil.rmtree(data['temp_dir'])
                    except Exception as cleanup_err:
                        logger.error(f"Error removing temp dir {data['temp_dir']} after processing: {cleanup_err}")


            # 4. Extract topics from combined content
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
    if not session_data.get('document_content'):
        flash('No document content found. Please upload a document.', 'warning')
        return redirect(url_for('index'))
    
    return render_template(
        'results.html',
        topics=session_data.get('topics', {}),
        granularity=session_data.get('current_granularity', 50)
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
    start_time = datetime.now()
    try:
        session_id = session.get('session_id')
        output_format = request.form.get('format', 'markdown')
        process_images = request.form.get('process_images', 'false') == 'true'

        if not session_id or session_id not in sessions_data:
            flash('Nessuna sessione attiva trovata. Per favore carica un documento.', 'warning')
            return redirect(url_for('index'))

        session_data = sessions_data[session_id]
        topics_dict = session_data.get('topics', {})
        document_content = session_data.get('document_content', '')
        document_id = session_data.get('document_id') # Get document ID

        if not topics_dict:
            flash('Nessun argomento trovato. Per favore aggiusta la granularità e riprova.', 'warning')
            return render_template(
                'results.html',
                topics={},
                granularity=session_data.get('current_granularity', 50),
                notes={},
                selected_format=output_format
            )

        if not document_id:
             flash('ID documento non trovato nella sessione. Impossibile processare le immagini.', 'danger')
             process_images = False # Disable image processing if document_id is missing

        # Ottieni gli argomenti dal database per questo documento
        db_topics = Topic.query.filter_by(document_id=document_id).all()
        topic_map = {topic.topic_id: topic for topic in db_topics}

        # Initialize generated_notes for this run
        generated_notes = {}
        successfully_processed_count = 0
        errors_encountered = []

        logger.info(f"Avvio generazione automatica per {len(topics_dict)} argomenti. Formato: {output_format}, Immagini: {process_images}")

        # --- Determine persistent image folder path ---
        images_folder = None
        if document_id:
            document_upload_folder = os.path.join(app.config['UPLOAD_FOLDER'], str(document_id))
            images_folder = os.path.join(document_upload_folder, 'images')
            if not os.path.exists(images_folder):
                images_folder = None

        # --- FIX: Iterate over topics_dict, not generated_notes ---
        for topic_id, topic_data in topics_dict.items():
            logger.info(f"Processando argomento: {topic_data['name']} ({topic_id})")
            try:
                # Controlla se esiste già una nota nel DB per questo formato
                existing_db_note = None
                db_topic_obj = topic_map.get(topic_id) # Trova l'oggetto Topic del DB corrispondente

                if db_topic_obj:
                    existing_db_note = Note.query.filter_by(
                        topic_id=db_topic_obj.id,
                        format=output_format
                    ).first()

                if existing_db_note:
                    logger.info(f"Utilizzo nota esistente dal DB per l'argomento: {topic_data['name']}")
                    # Store the existing note content in the current run's generated_notes
                    generated_notes[topic_id] = {
                        'name': topic_data['name'],
                        'content': existing_db_note.content,
                        'format': output_format
                    }
                    successfully_processed_count += 1
                    continue # Passa al prossimo argomento nel ciclo

                # --- Generazione Nuova Nota ---
                logger.info(f"Estrazione informazioni per l'argomento: {topic_data['name']}")
                topic_info = document_processor.extract_topic_information(
                    document_content,
                    topic_data['name'],
                    gemini_client
                )

                if isinstance(topic_info, str) and "Error: Quota limit reached" in topic_info:
                    logger.warning(f"Quota API raggiunta durante estrazione per {topic_data['name']}")
                    errors_encountered.append(f"Quota API raggiunta durante l'estrazione per '{topic_data['name']}'.")
                    continue # Prova il prossimo argomento

                logger.info(f"Miglioramento informazioni per l'argomento: {topic_data['name']}")
                enhanced_info = gemini_client.enhance_topic_info(
                    topic_data['name'],
                    topic_info
                )

                if isinstance(enhanced_info, str) and "Error: Quota limit reached" in enhanced_info:
                    logger.warning(f"Quota API raggiunta durante miglioramento per {topic_data['name']}")
                    errors_encountered.append(f"Quota API raggiunta durante il miglioramento per '{topic_data['name']}'.")
                    continue # Prova il prossimo argomento

                # --- Processamento Immagini (Opzionale) ---
                image_content = ""
                if process_images and images_folder and db_topic_obj:
                    try:
                        logger.info(f"Ricerca immagini per l'argomento: {topic_data['name']} in {images_folder}")

                        image_files = [f for f in os.listdir(images_folder)
                                       if os.path.isfile(os.path.join(images_folder, f))
                                       and f.lower().endswith(('.png', '.jpg', '.jpeg'))]

                        db_analyses = ImageAnalysis.query.filter_by(topic_id=db_topic_obj.id).all()
                        existing_analyses = {a.filename: a.analysis_result for a in db_analyses}
                        processed_one_image = False

                        for img_file in image_files:
                            single_image_path = os.path.join(images_folder, img_file)

                            if img_file in existing_analyses:
                                logger.info(f"Utilizzo analisi immagine esistente dal DB: {img_file}")
                                description = existing_analyses[img_file]
                                if description and not description.startswith('Error:'):
                                    if not image_content: image_content = "\n\n## Contenuto Visivo Correlato\n\n"
                                    image_content += f"### Figura: {img_file}\n\n{description}\n\n"
                            elif not processed_one_image:
                                logger.info(f"Processando nuova immagine: {img_file}")

                                temp_image_analysis_dir = tempfile.mkdtemp()
                                temp_image_path_for_analysis = os.path.join(temp_image_analysis_dir, img_file)
                                shutil.copy(single_image_path, temp_image_path_for_analysis)
                                topic_images = image_analyzer.analyze_images_for_topics(
                                    temp_image_analysis_dir,
                                    {topic_id: topic_data}
                                )
                                shutil.rmtree(temp_image_analysis_dir) # Clean up temp dir

                                description = None
                                if (topic_images and topic_id in topic_images and
                                    topic_images[topic_id] and img_file in topic_images[topic_id]):
                                    description = topic_images[topic_id][img_file]

                                if description:
                                    if not image_content: image_content = "\n\n## Contenuto Visivo Correlato\n\n"
                                    image_content += f"### Figura: {img_file}\n\n{description}\n\n"

                                    # --- FIX: Add the 'path' value ---
                                    # Store relative path within the document's upload folder
                                    relative_image_path = os.path.join('images', img_file)

                                    img_analysis = ImageAnalysis(
                                        filename=img_file,
                                        path=relative_image_path, # Add the path here
                                        topic_id=db_topic_obj.id,
                                        analysis_result=description
                                    )
                                    # --- End Fix ---
                                    db.session.add(img_analysis)
                                    db.session.commit() # Commit should now work
                                    logger.info(f"Analisi immagine {img_file} salvata nel DB.")
                                    processed_one_image = True

                    except Exception as img_err:
                        logger.error(f"Errore durante processamento immagini per {topic_data['name']}: {str(img_err)}")
                        errors_encountered.append(f"Errore imprevisto durante l'analisi delle immagini per '{topic_data['name']}'.")

                # --- Combinazione e Formattazione ---
                combined_content = enhanced_info
                if image_content:
                    combined_content += image_content

                formatted_content = format_converter.convert(
                    topic_data['name'],
                    combined_content,
                    output_format
                )

                # --- Salvataggio Nota nel DB ---
                if db_topic_obj:
                    existing_note = Note.query.filter_by(
                        topic_id=db_topic_obj.id,
                        format=output_format
                    ).first()

                    if existing_note:
                        logger.info(f"Aggiornamento nota esistente nel DB per {topic_data['name']}")
                        existing_note.content = formatted_content
                        existing_note.updated_at = datetime.utcnow()
                    else:
                        logger.info(f"Creazione nuova nota nel DB per {topic_data['name']}")
                        note = Note(
                            content=formatted_content,
                            format=output_format,
                            topic_id=db_topic_obj.id
                        )
                        db.session.add(note)

                    db.session.commit()

                    # Store the newly generated note content
                    generated_notes[topic_id] = {
                        'name': topic_data['name'],
                        'content': formatted_content,
                        'format': output_format
                    }
                    successfully_processed_count += 1
                else:
                    logger.warning(f"Impossibile salvare la nota per l'argomento {topic_data['name']} ({topic_id}) perché non trovato nel DB.")
                    errors_encountered.append(f"Impossibile trovare l'argomento '{topic_data['name']}' nel database per salvare la nota.")

            except Exception as e:
                db.session.rollback()
                logger.error(f"Errore durante il processamento dell'argomento {topic_data['name']} ({topic_id}): {str(e)}", exc_info=True)
                errors_encountered.append(f"Errore imprevisto durante il processamento di '{topic_data['name']}': {str(e)}")

        logger.info(f"Ciclo di generazione completato. Argomenti processati con successo: {successfully_processed_count}/{len(topics_dict)}")

        # --- HYPERLINKING ---
        if generated_notes:
             logger.info("Aggiunta hyperlink alle note generate...")
             try:
                 all_notes_for_hyperlinking = {}
                 for t_id, t_data in topics_dict.items():
                     if t_id in generated_notes:
                         all_notes_for_hyperlinking[t_id] = generated_notes[t_id]
                     elif topic_map.get(t_id):
                         db_note = Note.query.filter_by(topic_id=topic_map[t_id].id, format=output_format).first()
                         if db_note:
                             all_notes_for_hyperlinking[t_id] = {'name': t_data['name'], 'content': db_note.content, 'format': output_format}

                 notes_with_links = format_converter.add_hyperlinks(
                     all_notes_for_hyperlinking,
                     topics_dict,
                     output_format
                 )

                 logger.info("Aggiornamento note nel DB con hyperlink...")
                 for linked_topic_id, linked_note_data in notes_with_links.items():
                     db_topic_obj = topic_map.get(linked_topic_id)
                     if db_topic_obj:
                         db_note = Note.query.filter_by(
                             topic_id=db_topic_obj.id,
                             format=output_format
                         ).first()
                         if db_note and db_note.content != linked_note_data['content']:
                             db_note.content = linked_note_data['content']
                             db_note.updated_at = datetime.utcnow()

                 db.session.commit()
                 generated_notes = notes_with_links
                 logger.info("Hyperlink aggiunti e salvati nel DB.")

             except Exception as link_err:
                 db.session.rollback()
                 logger.error(f"Errore durante l'aggiunta degli hyperlink: {str(link_err)}", exc_info=True)
                 errors_encountered.append(f"Errore durante l'aggiornamento dei link interni: {str(link_err)}")

        # Save the results of THIS run to the session
        session_data['generated_notes'] = generated_notes
        session_data['selected_format'] = output_format

        total_time = (datetime.now() - start_time).total_seconds()
        logger.info(f"Generazione completata in {total_time:.2f} secondi.")

        if not errors_encountered and successfully_processed_count == len(topics_dict):
            flash(f'Tutte le {successfully_processed_count} note sono state generate con successo in {total_time:.2f} secondi!', 'success')
        elif successfully_processed_count > 0:
            flash(f'Generazione completata con {successfully_processed_count}/{len(topics_dict)} note generate. Tempo totale: {total_time:.2f} secondi. Controlla i messaggi per eventuali errori.', 'warning')
            for error in errors_encountered:
                flash(error, 'danger')
        else:
            flash(f'Generazione fallita. Nessuna nota è stata generata. Tempo totale: {total_time:.2f} secondi.', 'danger')
            for error in errors_encountered:
                flash(error, 'danger')

        return render_template(
            'results.html',
            topics=topics_dict,
            granularity=session_data.get('current_granularity', 50),
            notes=generated_notes,
            selected_format=output_format
        )

    except Exception as e:
        db.session.rollback()
        logger.error(f"Errore generale durante la generazione automatica delle note: {str(e)}", exc_info=True)
        flash(f'Errore imprevisto durante la generazione delle note: {str(e)}', 'danger')
        session_id = session.get('session_id')
        if session_id and session_id in sessions_data:
             session_data = sessions_data[session_id]
             return render_template(
                 'results.html',
                 topics=session_data.get('topics', {}),
                 granularity=session_data.get('current_granularity', 50),
                 notes=session_data.get('generated_notes', {}),
                 selected_format=request.form.get('format', 'markdown')
             )
        else:
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
                image_references = re.findall(r"### Figura: (.*\.(?:png|jpg|jpeg|gif|bmp))", note_content, re.IGNORECASE)
                # --- End Fix ---
                logger.debug(f"Found image references in note '{topic_data['name']}': {image_references}") # DEBUG

                for img_filename in image_references:
                    if img_filename not in added_images:
                        image_path = os.path.join(images_folder, img_filename)
                        logger.debug(f"Checking for image path: {image_path}") # DEBUG
                        logger.debug(f"Does image path exist? {os.path.exists(image_path)}") # DEBUG
                        if os.path.exists(image_path):
                            try:
                                zip_image_path = os.path.join('images', img_filename)
                                zf.write(image_path, arcname=zip_image_path)
                                added_images.add(img_filename)
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
        viewing_topic=topic_data
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
