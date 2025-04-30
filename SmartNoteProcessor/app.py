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
gemini_client = GeminiClient(api_key=os.environ.get("GEMINI_API_KEY", ""))
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
    if 'file' not in request.files:
        flash('No file part', 'danger')
        return redirect(request.url)
    
    file = request.files['file']
    
    if file.filename == '':
        flash('No selected file', 'danger')
        return redirect(request.url)
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        session_id = session.get('session_id')
        
        # Create a unique folder for this session's uploads
        upload_folder = os.path.join(tempfile.gettempdir(), f"smart_notes_{session_id}")
        if not os.path.exists(upload_folder):
            os.makedirs(upload_folder)
        
        # Make a subfolder for images if it doesn't exist
        images_folder = os.path.join(upload_folder, "images")
        if not os.path.exists(images_folder):
            os.makedirs(images_folder)
        
        # Save the file
        file_path = os.path.join(upload_folder, filename)
        file.save(file_path)
        
        try:
            # Process document
            document_content = document_processor.extract_text(file_path, file.filename)
            
            # Extract images if it's a PDF file
            if filename.lower().endswith('.pdf'):
                try:
                    # Import PDF specific libraries here
                    from pdfminer.high_level import extract_text as pdf_extract
                    from pdfminer.layout import LAParams
                    import fitz  # PyMuPDF
                    
                    # Extract images from PDF using PyMuPDF
                    pdf_document = fitz.open(file_path)
                    
                    # Iterate through pages and extract images
                    image_count = 0
                    for page_num in range(len(pdf_document)):
                        page = pdf_document[page_num]
                        image_list = page.get_images(full=True)
                        
                        for img_index, img in enumerate(image_list):
                            xref = img[0]
                            base_image = pdf_document.extract_image(xref)
                            image_bytes = base_image["image"]
                            
                            # Determine file extension based on image type
                            image_ext = base_image["ext"]
                            image_filename = f"page{page_num+1}_img{img_index+1}.{image_ext}"
                            
                            with open(os.path.join(images_folder, image_filename), "wb") as img_file:
                                img_file.write(image_bytes)
                                image_count += 1
                    
                    logger.info(f"Extracted {image_count} images from the PDF")
                    
                except ImportError as ie:
                    logger.warning(f"PDF image extraction skipped: {str(ie)}")
                except Exception as pdf_err:
                    logger.error(f"Error extracting images from PDF: {str(pdf_err)}")
            
            # Ensure session ID exists in sessions_data dictionary
            if session_id not in sessions_data:
                sessions_data[session_id] = {
                    'document_content': '',
                    'topics': {},
                    'current_granularity': 50
                }
            
            # Store in session data for temporary use
            sessions_data[session_id]['document_content'] = document_content
            sessions_data[session_id]['upload_folder'] = upload_folder
            
            # Extract topics with default granularity
            granularity = 50  # Default granularity
            topics_dict = topic_extractor.extract_topics(document_content, granularity)
            sessions_data[session_id]['topics'] = topics_dict
            sessions_data[session_id]['current_granularity'] = granularity
            
            # Store document in database
            title = filename.rsplit('.', 1)[0]  # Use filename without extension as title
            file_type = filename.rsplit('.', 1)[1].lower()
            
            # Create new Document record
            document = Document(
                title=title,
                content=document_content,
                filename=filename,
                file_type=file_type
            )
            
            # Add and commit to get the document ID
            db.session.add(document)
            db.session.commit()
            
            # Store document ID in session for future reference
            sessions_data[session_id]['document_id'] = document.id
            
            # Create Topic records for each extracted topic
            for topic_id, topic_data in topics_dict.items():
                topic = Topic(
                    topic_id=topic_id,
                    name=topic_data['name'],
                    description=topic_data.get('description', ''),
                    document_id=document.id
                )
                db.session.add(topic)
            
            # Commit all topic records
            db.session.commit()
            
            flash('Document uploaded and processed successfully!', 'success')
            return redirect(url_for('results'))
        
        except Exception as e:
            logger.error(f"Error processing document: {str(e)}")
            flash(f'Error processing document: {str(e)}', 'danger')
            # Clean up temp folder on error
            try:
                shutil.rmtree(upload_folder)
            except:
                pass
            return redirect(url_for('index'))
    else:
        flash(f'Invalid file type. Allowed types: {", ".join(ALLOWED_EXTENSIONS)}', 'danger')
    
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

import os
import shutil
from flask import Flask, request, session, flash, redirect, url_for, render_template
from datetime import datetime
# Assumendo che le tue classi Model (Topic, Note, ImageAnalysis) e db siano definite
# Assumendo che document_processor, gemini_client, image_analyzer, format_converter
# e logger siano inizializzati correttamente
# Assumendo che sessions_data sia un dizionario globale o gestito diversamente

# ... (altre importazioni e setup dell'app Flask) ...

@app.route('/generate_notes', methods=['POST']) # Cambiato nome route per chiarezza
def generate_notes():
    start_time = datetime.now() # Per logging del tempo totale
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
        document_id = session_data.get('document_id')

        if not topics_dict:
            flash('Nessun argomento trovato. Per favore aggiusta la granularità e riprova.', 'warning')
            # Ritorna alla pagina dei risultati anche se vuota
            return render_template(
                'results.html',
                topics={},
                granularity=session_data.get('current_granularity', 50),
                notes={},
                selected_format=output_format
            )

        # Ottieni gli argomenti dal database per questo documento
        db_topics = Topic.query.filter_by(document_id=document_id).all()
        topic_map = {topic.topic_id: topic for topic in db_topics} # Mappa topic_id -> oggetto DB Topic

        # Recupera/Inizializza le note generate nella sessione
        generated_notes = session_data.get('generated_notes', {})
        processed_topic_ids = set(generated_notes.keys())
        successfully_processed_count = 0
        errors_encountered = []

        logger.info(f"Avvio generazione automatica per {len(topics_dict)} argomenti. Formato: {output_format}, Immagini: {process_images}")

        # --- INIZIO CICLO SU TUTTI GLI ARGOMENTI ---
        for topic_id, topic_data in topics_dict.items():

            logger.info(f"Processando argomento: {topic_data['name']} ({topic_id})")

            # Blocco try/except per singolo argomento, per permettere al ciclo di continuare
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
                    # Potremmo decidere di interrompere tutto qui con 'break' o continuare
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
                if process_images and 'upload_folder' in session_data and db_topic_obj:
                    try:
                        upload_folder = session_data.get('upload_folder')
                        images_folder = os.path.join(upload_folder, 'images')
                        if upload_folder and os.path.exists(images_folder):
                            logger.info(f"Ricerca immagini per l'argomento: {topic_data['name']}")

                            image_files = [f for f in os.listdir(images_folder)
                                           if os.path.isfile(os.path.join(images_folder, f))
                                           and f.lower().endswith(('.png', '.jpg', '.jpeg'))]

                            # Controlla analisi esistenti nel DB per questo argomento
                            db_analyses = ImageAnalysis.query.filter_by(topic_id=db_topic_obj.id).all()
                            existing_analyses = {a.filename: a.analysis_result for a in db_analyses}
                            processed_one_image = False # Flag per processare solo una nuova immagine per argomento

                            for img_file in image_files:
                                single_image_path = os.path.join(images_folder, img_file)

                                # Se esiste già analisi nel DB, usala
                                if img_file in existing_analyses:
                                    logger.info(f"Utilizzo analisi immagine esistente dal DB: {img_file}")
                                    description = existing_analyses[img_file]
                                    if description and not description.startswith('Error:'):
                                        if not image_content: image_content = "\n\n## Contenuto Visivo Correlato\n\n"
                                        image_content += f"### Figura: {img_file}\n\n{description}\n\n"
                                        # Considera di aggiungere solo la prima immagine trovata (anche se già analizzata)
                                        # per evitare note troppo lunghe
                                        # processed_one_image = True
                                        # break
                                # Se non esiste e non ne abbiamo ancora processata una NUOVA per questo argomento
                                elif not processed_one_image:
                                    logger.info(f"Processando nuova immagine: {img_file}")

                                    # Crea cartella temporanea solo con questa immagine
                                    temp_image_folder = os.path.join(upload_folder, f'temp_image_{topic_id}')
                                    os.makedirs(temp_image_folder, exist_ok=True)
                                    temp_image_path = os.path.join(temp_image_folder, img_file)
                                    shutil.copy(single_image_path, temp_image_path)

                                    topic_images = image_analyzer.analyze_images_for_topics(
                                        temp_image_folder,
                                        {topic_id: topic_data} # Analizza solo per l'argomento corrente
                                    )
                                    shutil.rmtree(temp_image_folder, ignore_errors=True) # Pulisci temp

                                    if (topic_images and topic_id in topic_images and
                                        topic_images[topic_id] and img_file in topic_images[topic_id]):

                                        description = topic_images[topic_id][img_file]

                                        if isinstance(description, str) and "Error: Quota limit reached" in description:
                                            logger.warning(f"Quota API raggiunta durante analisi immagine {img_file}")
                                            errors_encountered.append(f"Quota API raggiunta durante l'analisi dell'immagine '{img_file}' per '{topic_data['name']}'.")
                                            # Non interrompere il ciclo, ma salta questa immagine
                                        elif isinstance(description, str) and description.startswith('Error:'):
                                             logger.warning(f"Errore analisi immagine {img_file}: {description}")
                                             errors_encountered.append(f"Errore analisi immagine '{img_file}' per '{topic_data['name']}'.")
                                        else:
                                            # Aggiungi al contenuto e salva nel DB
                                            if not image_content: image_content = "\n\n## Contenuto Visivo Correlato\n\n"
                                            image_content += f"### Figura: {img_file}\n\n{description}\n\n"

                                            img_analysis = ImageAnalysis(
                                                filename=img_file,
                                                path=single_image_path, # Salva il percorso originale
                                                topic_id=db_topic_obj.id,
                                                analysis_result=description
                                            )
                                            db.session.add(img_analysis)
                                            # Commit parziale per l'analisi immagine
                                            db.session.commit()
                                            logger.info(f"Analisi immagine {img_file} salvata nel DB.")
                                            processed_one_image = True # Abbiamo processato una nuova immagine
                                    # Processa solo una nuova immagine per argomento per evitare timeout
                                    # break # Commentato: potremmo voler aggiungere tutte le immagini pre-analizzate

                    except Exception as img_err:
                        logger.error(f"Errore durante processamento immagini per {topic_data['name']}: {str(img_err)}")
                        errors_encountered.append(f"Errore imprevisto durante l'analisi delle immagini per '{topic_data['name']}'.")
                        # Non interrompere il ciclo principale

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
                    # Riprova a cercare la nota, potrebbe essere stata creata da un'altra richiesta
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

                    # Commit dopo ogni nota per salvare il progresso
                    db.session.commit()

                    # Aggiungi alla lista delle note generate in questa run
                    generated_notes[topic_id] = {
                        'name': topic_data['name'],
                        'content': formatted_content, # Inizialmente senza hyperlink
                        'format': output_format
                    }
                    successfully_processed_count += 1
                else:
                    logger.warning(f"Impossibile salvare la nota per l'argomento {topic_data['name']} ({topic_id}) perché non trovato nel DB.")
                    errors_encountered.append(f"Impossibile trovare l'argomento '{topic_data['name']}' nel database per salvare la nota.")


            except Exception as e:
                db.session.rollback() # Annulla le modifiche DB per questo argomento specifico in caso di errore
                logger.error(f"Errore durante il processamento dell'argomento {topic_data['name']} ({topic_id}): {str(e)}", exc_info=True)
                errors_encountered.append(f"Errore imprevisto durante il processamento di '{topic_data['name']}': {str(e)}")
                # Continua con il prossimo argomento

        # --- FINE CICLO ---
        logger.info(f"Ciclo di generazione completato. Argomenti processati con successo: {successfully_processed_count}/{len(topics_dict)}")

        # --- AGGIUNTA HYPERLINK (dopo che tutte le note base sono state generate) ---
        if generated_notes:
             logger.info("Aggiunta hyperlink alle note generate...")
             try:
                 # Assicurati che 'generated_notes' contenga tutte le note (sia quelle appena generate che quelle recuperate dal DB all'inizio)
                 all_notes_for_hyperlinking = {}
                 for t_id, t_data in topics_dict.items():
                     # Controlla se la nota è stata generata o recuperata in questa run
                     if t_id in generated_notes:
                         all_notes_for_hyperlinking[t_id] = generated_notes[t_id]
                     # Altrimenti, prova a recuperarla dal DB se non era stata recuperata prima
                     elif topic_map.get(t_id):
                         db_note = Note.query.filter_by(topic_id=topic_map[t_id].id, format=output_format).first()
                         if db_note:
                             all_notes_for_hyperlinking[t_id] = {'name': t_data['name'], 'content': db_note.content, 'format': output_format}

                 notes_with_links = format_converter.add_hyperlinks(
                     all_notes_for_hyperlinking, # Usa tutte le note disponibili
                     topics_dict,
                     output_format
                 )

                 # Aggiorna le note nel DB con gli hyperlink
                 logger.info("Aggiornamento note nel DB con hyperlink...")
                 for linked_topic_id, linked_note_data in notes_with_links.items():
                     db_topic_obj = topic_map.get(linked_topic_id)
                     if db_topic_obj:
                         db_note = Note.query.filter_by(
                             topic_id=db_topic_obj.id,
                             format=output_format
                         ).first()
                         if db_note and db_note.content != linked_note_data['content']: # Aggiorna solo se cambiato
                             db_note.content = linked_note_data['content']
                             db_note.updated_at = datetime.utcnow()

                 db.session.commit() # Commit finale per gli hyperlink
                 generated_notes = notes_with_links # Aggiorna la variabile locale con le note linkate
                 logger.info("Hyperlink aggiunti e salvati nel DB.")

             except Exception as link_err:
                 db.session.rollback()
                 logger.error(f"Errore durante l'aggiunta degli hyperlink: {str(link_err)}", exc_info=True)
                 errors_encountered.append(f"Errore durante l'aggiornamento dei link interni: {str(link_err)}")
                 # Le note senza link verranno comunque mostrate

        # Aggiorna la sessione con tutte le note processate (con o senza hyperlink)
        session_data['generated_notes'] = generated_notes
        session_data['selected_format'] = output_format
        sessions_data[session_id] = session_data # Salva l'aggiornamento nella struttura dati delle sessioni

        # Messaggi finali per l'utente
        total_time = (datetime.now() - start_time).total_seconds()
        logger.info(f"Generazione completata in {total_time:.2f} secondi.")

        if not errors_encountered and successfully_processed_count == len(topics_dict):
            flash(f'Tutte le {successfully_processed_count} note sono state generate con successo in {total_time:.2f} secondi!', 'success')
        elif successfully_processed_count > 0:
            flash(f'Generazione completata con {successfully_processed_count}/{len(topics_dict)} note generate. Tempo totale: {total_time:.2f} secondi. Controlla i messaggi per eventuali errori.', 'warning')
            for error in errors_encountered:
                flash(error, 'danger') # Mostra errori specifici
        else:
            flash(f'Generazione fallita. Nessuna nota è stata generata. Tempo totale: {total_time:.2f} secondi.', 'danger')
            for error in errors_encountered:
                flash(error, 'danger')

        return render_template(
            'results.html',
            topics=topics_dict,
            granularity=session_data.get('current_granularity', 50),
            notes=generated_notes, # Mostra le note generate (con hyperlink se riuscito)
            selected_format=output_format
        )

    except Exception as e:
        # Errore generale al di fuori del ciclo per singolo argomento
        db.session.rollback() # Assicurati che non ci siano commit parziali pendenti
        logger.error(f"Errore generale durante la generazione automatica delle note: {str(e)}", exc_info=True)
        flash(f'Errore imprevisto durante la generazione delle note: {str(e)}', 'danger')
        # Cerca di reindirizzare ai risultati mostrando quello che c'era prima dell'errore, se possibile
        session_id = session.get('session_id')
        if session_id and session_id in sessions_data:
             session_data = sessions_data[session_id]
             return render_template(
                 'results.html',
                 topics=session_data.get('topics', {}),
                 granularity=session_data.get('current_granularity', 50),
                 notes=session_data.get('generated_notes', {}), # Mostra note generate prima dell'errore
                 selected_format=request.form.get('format', 'markdown')
             )
        else:
             return redirect(url_for('index')) # Fallback se la sessione è persa

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
    
    # Create response with file
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
    upload_folder = session_data.get('upload_folder') # Get upload folder path
    
    if not generated_notes:
        flash('No notes generated yet', 'danger')
        return redirect(url_for('results'))
        
    images_folder = None
    if upload_folder and os.path.exists(upload_folder):
        images_folder = os.path.join(upload_folder, 'images')
        if not os.path.exists(images_folder):
            images_folder = None # Reset if images subfolder doesn't exist

    memory_file = io.BytesIO()
    added_images = set() # Keep track of added images to avoid duplicates

    with zipfile.ZipFile(memory_file, 'w') as zf:
        for topic_id, topic_data in generated_notes.items():
            format_extension = {
                'markdown': '.md',
                'html': '.html',
                'latex': '.tex'
            }.get(topic_data['format'], '.txt')
            
            note_filename = f"{topic_data['name'].replace(' ', '_')}{format_extension}"
            note_content = topic_data['content']
            
            # --- Add image handling ---
            if images_folder:
                # Find image references (adjust regex based on how images are referenced)
                # Example: looking for "### Figure: image_name.ext"
                image_references = re.findall(r"### Figure: (.*\.(?:png|jpg|jpeg|gif|bmp))", note_content, re.IGNORECASE)
                
                for img_filename in image_references:
                    if img_filename not in added_images:
                        image_path = os.path.join(images_folder, img_filename)
                        if os.path.exists(image_path):
                            try:
                                # Add image to a subfolder 'images' in the zip
                                zip_image_path = os.path.join('images', img_filename)
                                zf.write(image_path, arcname=zip_image_path)
                                added_images.add(img_filename)
                                logger.info(f"Added image '{img_filename}' to zip.")
                                
                                # (Optional) Update note content with relative path
                                # This depends heavily on the format and how images are linked
                                if topic_data['format'] == 'markdown':
                                     # Example: Replace "### Figure: name.jpg" with actual image tag
                                     # This is a simple replacement, might need more robust logic
                                     note_content = note_content.replace(
                                         f"### Figure: {img_filename}",
                                         f"![{img_filename}](images/{img_filename})"
                                     )
                                elif topic_data['format'] == 'html':
                                     # Example for HTML
                                     note_content = note_content.replace(
                                         f"### Figure: {img_filename}", # Assuming this placeholder exists
                                         f'<p><img src="images/{img_filename}" alt="{img_filename}"></p>'
                                     )
                                # Add similar logic for LaTeX if needed

                            except Exception as zip_err:
                                logger.error(f"Failed to add image {img_filename} to zip: {zip_err}")
                        else:
                             logger.warning(f"Referenced image not found: {image_path}")

            # --- End image handling ---

            # Write the (potentially modified) note content to the zip
            zf.writestr(note_filename, note_content)
    
    memory_file.seek(0)
    
    from flask import send_file
    return send_file(
        memory_file, 
        mimetype='application/zip',
        as_attachment=True,
        download_name='smart_notes_with_images.zip' # Updated filename
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
