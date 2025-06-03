import os
import logging
from datetime import datetime
import concurrent.futures # Aggiungi questo import
from models import Topic, Note, Document, db as database_session # Assicurati che db sia importato correttamente o passato
from flask import current_app

from utils.document_processor import DocumentProcessor
from utils.resumes_enhancer import ResumeesEnhancer
from utils.format_converter import FormatConverter
from utils.image_analyzer import ImageAnalyzer
from utils.merge_topics import MergeTopics

logger = logging.getLogger(__name__)

class SmartNotesOrchestrator:
    def __init__(self, document_processor, topic_extractor, openrouter_client, format_converter, image_analyzer, resumes_enhancer, db, app_config, flask_app):
        self.document_processor = document_processor
        self.topic_extractor = topic_extractor
        self.openrouter_client = openrouter_client
        self.format_converter = format_converter
        self.image_analyzer = image_analyzer
        self.resumes_enhancer = resumes_enhancer # Assign to self
        self.db = db
        self.flask_app = flask_app

    def _process_single_topic(self, topic_id_str, topic_data, combined_content, output_format, process_images_flag, document_upload_folder_path, db_topic_obj):
        _app = self.flask_app

        with _app.app_context():
            topic_name = topic_data.get('name', f"Topic {topic_id_str}")
            logger.info(f"Orchestrator (Thread): Processing topic '{topic_name}' (model ID: {topic_id_str})")

            existing_db_note = Note.query.filter_by(
                topic_id=db_topic_obj.id,
                format=output_format
            ).first()

            if existing_db_note:
                logger.info(f"Orchestrator (Thread): Using existing note from DB for topic: {topic_name}")
                return topic_id_str, {
                    'note_data': {
                        'name': topic_name,
                        'content': existing_db_note.content,
                        'format': output_format
                    },
                    'status': 'existing',
                    'new_image_analyses': [] # Aggiungi per consistenza
                }

            try:
                topic_info = self.document_processor.extract_resumes(combined_content, topic_name) # Corretto: usa self.document_processor
                if isinstance(topic_info, str) and "Error:" in topic_info:
                    logger.error(f"Orchestrator (Thread): Error extracting info for '{topic_name}': {topic_info}")
                    return topic_id_str, {'error': f"Error extracting info for '{topic_name}': {topic_info}", 'status': 'error'}
                logger.info(f"Orchestrator (Thread): Initial summary created for topic '{topic_name}'.")

                image_analysis_content_summary = ""
                current_topic_new_image_analyses = []
                if process_images_flag and document_upload_folder_path:
                    images_subfolder = os.path.join(document_upload_folder_path, 'images')
                    if os.path.exists(images_subfolder) and os.path.isdir(images_subfolder):
                        # Corretta la chiamata e recupero degli oggetti ImageAnalysis
                        image_analysis_content_summary, current_topic_new_image_analyses = self.image_analyzer.analyze_images_and_get_summary(
                            {topic_id_str: topic_data}, images_subfolder, self.db.session # Passa self.db.session per le query sui Topic se necessario
                        )
                        logger.info(f"Orchestrator (Thread): Image analysis summary added for topic '{topic_name}'.")
                    else:
                        logger.info(f"Orchestrator (Thread): Images subfolder not found or not a directory: {images_subfolder}")
                
                resume_with_images = topic_info + "\n --- \n" + image_analysis_content_summary
                
                enhanced_info = self.resumes_enhancer.enhance_resumes(topic_name, resume_with_images, output_format) # Corretto: usa self.resumes_enhancer
                
                print("Enhanced Info:", enhanced_info)  # Debugging line
                
                if isinstance(enhanced_info, str) and "Error:" in enhanced_info:
                    logger.error(f"Orchestrator (Thread): Error enhancing info for '{topic_name}': {enhanced_info}")
                    return topic_id_str, {'error': f"Error enhancing info for '{topic_name}': {enhanced_info}", 'status': 'error'}

                formatted_content = self.format_converter.convert(topic_name, enhanced_info, output_format) # Corretto: usa self.format_converter

                new_note_obj = Note(
                    content=formatted_content,
                    format=output_format,
                    topic_id=db_topic_obj.id
                )
                
                return topic_id_str, {
                    'note_object': new_note_obj,
                    'note_data': {
                        'name': topic_name,
                        'content': formatted_content,
                        'format': output_format
                    },
                    'status': 'new',
                    'new_image_analyses': current_topic_new_image_analyses # Restituisci gli oggetti ImageAnalysis
                }
            except Exception as e:
                logger.error(f"Orchestrator (Thread): Error processing topic '{topic_name}': {str(e)}", exc_info=True)
                return topic_id_str, {'error': f"Error processing topic '{topic_name}': {str(e)}", 'status': 'error'}

    def process_and_generate(self, primary_document_id, combined_content, topics_dict, output_format, process_images_flag, document_upload_folder_path):
        generated_notes_for_return = {}
        successfully_processed_count = 0
        errors_encountered = []
        new_note_objects_to_commit = []
        all_new_image_analyses_to_commit = [] # Lista per raccogliere tutti gli ImageAnalysis

        db_document = Document.query.get(primary_document_id)
        if not db_document:
            errors_encountered.append(f"Document with ID {primary_document_id} not found.")
            logger.error(f"Orchestrator: Document with ID {primary_document_id} not found.")
            return {}, errors_encountered, 0

        db_topics_for_doc = Topic.query.filter_by(document_id=primary_document_id).all()
        topic_map_by_model_id = {t.topic_id: t for t in db_topics_for_doc}

        logger.info(f"Orchestrator: Starting parallel note generation for document ID {primary_document_id}, {len(topics_dict)} topics. Format: {output_format}, Images: {process_images_flag}")

        num_workers = min(10, (os.cpu_count() or 1) + 4)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            future_to_topic = {}
            for topic_id_str, topic_data in topics_dict.items():
                db_topic_obj = topic_map_by_model_id.get(topic_id_str)
                if not db_topic_obj:
                    logger.warning(f"Orchestrator: DB Topic object not found for model topic_id {topic_id_str}. Skipping note generation for '{topic_data.get('name', '')}'.")
                    errors_encountered.append(f"Could not find topic '{topic_data.get('name', '')}' in the database to associate the note.")
                    continue
                
                future = executor.submit(
                    self._process_single_topic,
                    topic_id_str, topic_data, combined_content, output_format,
                    process_images_flag, document_upload_folder_path, db_topic_obj
                )
                future_to_topic[future] = topic_id_str

            for future in concurrent.futures.as_completed(future_to_topic):
                topic_id_str_processed = future_to_topic[future]
                try:
                    _, result = future.result() # Il primo elemento della tupla (topic_id_str) non è usato qui

                    if result.get('status') == 'error': # Usa .get() per sicurezza
                        errors_encountered.append(result['error'])
                    elif result.get('status') == 'existing':
                        generated_notes_for_return[topic_id_str_processed] = result['note_data']
                        successfully_processed_count += 1
                        if result.get('new_image_analyses'): # Anche se 'existing', potrebbero esserci nuove analisi di immagini se la logica lo permette
                            all_new_image_analyses_to_commit.extend(result['new_image_analyses'])
                    elif result.get('status') == 'new':
                        new_note_objects_to_commit.append(result['note_object'])
                        generated_notes_for_return[topic_id_str_processed] = result['note_data']
                        successfully_processed_count += 1
                        if result.get('new_image_analyses'):
                            all_new_image_analyses_to_commit.extend(result['new_image_analyses'])
                except Exception as exc:
                    topic_name_for_error = topics_dict.get(topic_id_str_processed, {}).get('name', topic_id_str_processed)
                    logger.error(f"Orchestrator: Exception processing topic '{topic_name_for_error}': {exc}", exc_info=True)
                    errors_encountered.append(f"Exception processing topic '{topic_name_for_error}': {str(exc)}")
        
        # Commit batch di Note e ImageAnalysis
        if new_note_objects_to_commit or all_new_image_analyses_to_commit:
            try:
                if new_note_objects_to_commit:
                    self.db.session.add_all(new_note_objects_to_commit)
                    logger.info(f"Orchestrator: Staged {len(new_note_objects_to_commit)} new notes for commit.")
                if all_new_image_analyses_to_commit:
                    self.db.session.add_all(all_new_image_analyses_to_commit)
                    logger.info(f"Orchestrator: Staged {len(all_new_image_analyses_to_commit)} new image analyses for commit.")
                
                self.db.session.commit()
                logger.info("Orchestrator: Batch committed new notes and image analyses to the database.")
            except Exception as e:
                self.db.session.rollback()
                logger.error(f"Orchestrator: Error batch committing new items: {str(e)}", exc_info=True)
                errors_encountered.append(f"Database error during batch commit: {str(e)}")

        if generated_notes_for_return:
            logger.info("Orchestrator: Adding hyperlinks to notes.")
            try:
                all_notes_for_hyperlinking = {}
                for t_id_str_link, t_data_link in topics_dict.items():
                    if t_id_str_link in generated_notes_for_return:
                        all_notes_for_hyperlinking[t_id_str_link] = generated_notes_for_return[t_id_str_link]
                    else:
                        db_topic_for_link = topic_map_by_model_id.get(t_id_str_link)
                        if db_topic_for_link:
                            db_note_for_link = Note.query.filter_by(topic_id=db_topic_for_link.id, format=output_format).first()
                            if db_note_for_link:
                                 all_notes_for_hyperlinking[t_id_str_link] = {'name': t_data_link['name'], 'content': db_note_for_link.content, 'format': output_format}
                
                notes_with_links = self.format_converter.add_hyperlinks(
                    all_notes_for_hyperlinking, topics_dict, output_format
                )

                notes_to_update_in_db = []
                for linked_topic_id_str, linked_note_data in notes_with_links.items():
                    db_topic_obj_for_link_update = topic_map_by_model_id.get(linked_topic_id_str)
                    if db_topic_obj_for_link_update:
                        db_note_to_update = Note.query.filter_by(
                            topic_id=db_topic_obj_for_link_update.id,
                            format=output_format
                        ).first()
                        if db_note_to_update and db_note_to_update.content != linked_note_data['content']:
                            db_note_to_update.content = linked_note_data['content']
                            db_note_to_update.updated_at = datetime.utcnow()
                            notes_to_update_in_db.append(db_note_to_update)
                
                if notes_to_update_in_db:
                    self.db.session.add_all(notes_to_update_in_db)
                    self.db.session.commit()
                
                generated_notes_for_return = notes_with_links
                logger.info("Orchestrator: Hyperlinks added and notes updated in DB.")
            except Exception as link_err:
                self.db.session.rollback()
                logger.error(f"Orchestrator: Error during hyperlinking: {str(link_err)}", exc_info=True)
                errors_encountered.append(f"Error adding internal links: {str(link_err)}")

        if output_format == 'markdown' and generated_notes_for_return:
            intro_content = "# Table of Contents\n\nThis document provides an overview and links to all generated notes:\n\n"
            sorted_notes_for_index = sorted(
                [(tid, data) for tid, data in generated_notes_for_return.items() if tid != "000_index_introduction_page"],
                key=lambda item: item[1].get('name', '')
            )
            for topic_id_str_idx, note_data_idx in sorted_notes_for_index:
                note_name_idx = note_data_idx.get('name', f"Topic {topic_id_str_idx}")
                if note_data_idx.get('format') == 'markdown':
                    filename_idx = f"{note_name_idx.replace(' ', '_').replace('/', '_')}.md"
                    intro_content += f"- [{note_name_idx}](./{filename_idx})\n"
            
            generated_notes_for_return["000_index_introduction_page"] = {
                'name': "Introduction", 
                'content': intro_content,
                'format': 'markdown' 
            }
            logger.info("Orchestrator: Introductory Markdown file content generated.")

        return generated_notes_for_return, errors_encountered, successfully_processed_count
    
    def create_unified_title(self, topic_titles):
        """
        Crea un titolo unificato per i topic mergiati.
        Esempio: "Topic1, Topic2, Topic3" -> "Topic1 + Topic2 + Topic3"
        """
        if not topic_titles:
            return "Merged Topic"
        # Pulisci eventuali spazi e crea il titolo
        
        unified_title = MergeTopics.merge_topics(self, topic_titles)

        return unified_title
    
    def _classify_instruction_type(self, user_instruction: str) -> str:
        """
        Classifica l'istruzione dell'utente come 'modification_request' o 'question'.
        """
        prompt = f"""Classify the following user instruction.
Is it primarily a request to modify or change existing text, or is it primarily a question asking for information?
Respond with only 'modification_request' or 'question'.

User instruction: "{user_instruction}"
Classification:"""
        try:
            logger.info(f"Classifying user instruction: {user_instruction[:100]}...") # MODIFICATO: logger -> logger
            classification = self.openrouter_client.classify_instruction(
                prompt=prompt
            ).strip().lower()
            logger.info(f"Instruction classified as: {classification}")
            if "modification_request" in classification:
                return "modification_request"
            elif "question" in classification:
                return "question"
            return "unknown" # Fallback
        except Exception as e:
            logger.error(f"Error classifying user instruction: {e}", exc_info=True)
            return "unknown" # Fallback in caso di errore

    def _answer_user_question(self, user_question: str, generated_notes: dict, document_content_full: str, chat_history: list) -> str: # Aggiunto chat_history
        """
        Genera una risposta alla domanda dell'utente basandosi sul contesto disponibile, inclusa la cronologia della chat.
        """
        logger.info(f"Attempting to answer user question: {user_question[:100]}... with chat history.")

        summaries_context = "\n\n---\n\n".join(
            [f"Topic: {note_data.get('name', topic_id)}\nSummary:\n{note_data.get('content', '')}"
             for topic_id, note_data in generated_notes.items()]
        )
        if not summaries_context:
            summaries_context = "No summaries are currently available to provide context."

        truncated_doc_content = document_content_full[:20000] if document_content_full else "Full document content not provided."
        if len(document_content_full) > 20000:
            truncated_doc_content += "\n[...content truncated due to length...]"

        # Formatta la cronologia della chat per il prompt
        formatted_chat_history = "\n".join(
            [f"{item['sender'].capitalize()}: {item['message']}" for item in chat_history]
        )
        if not formatted_chat_history:
            formatted_chat_history = "No previous conversation history."


        prompt = f"""You are a helpful AI assistant. A user has generated several summaries from a document and is now asking a question.
Answer the user's question based on all the provided context, including the previous conversation.

Previous Conversation History:
---
{formatted_chat_history}
---

User's Current Question:
---
{user_question}
---

Context from Generated Summaries (these are the summaries of different topics from the document):
---
{summaries_context}
---

Full Original Document Content (use this for deeper context if needed, be mindful it might be truncated):
---
{truncated_doc_content}
---

Based on all the available information (previous conversation, current question, summaries, and full document), provide a comprehensive answer to the user's current question.
If the information is not found in the provided context, clearly state that.
Answer:
"""
        try:
            ai_answer = self.openrouter_client.user_request( 
                prompt=prompt,
                model="openai/gpt-4o-mini" # Considera un modello più potente per gestire il contesto esteso
            )
            if not ai_answer:
                return "I couldn't generate an answer at this time. Please try again."
            return ai_answer.strip()
        except Exception as e:
            logger.error(f"Error generating answer for question '{user_question[:50]}...': {e}", exc_info=True)
            return "I encountered an error while trying to answer your question."

    def apply_user_instruction(self, user_instruction, generated_notes, topics_dict, document_id, document_content_full, chat_history: list): # Aggiunto chat_history
        if not user_instruction:
            logger.warning("User instruction is empty. No changes will be applied.")
            return generated_notes, "empty_instruction", "Please provide an instruction."

        instruction_type = self._classify_instruction_type(user_instruction)
        logger.info(f"User instruction type: {instruction_type}")

        if instruction_type == "modification_request":
            # La modifica dei riassunti di solito non necessita della cronologia della chat,
            # ma se necessario, puoi passargliela. Per ora, _handle_modification_request non la usa.
            updated_notes_session, status_mod = self._handle_modification_request(user_instruction, generated_notes, topics_dict, document_id, document_content_full)
            return updated_notes_session, status_mod, "Modifications have been applied to the summaries."


        elif instruction_type == "question":
            logger.info("Processing as a question.")
            ai_answer = self._answer_user_question(user_instruction, generated_notes, document_content_full, chat_history) # Passa chat_history
            return generated_notes, "question_answered", ai_answer

        else: 
            logger.warning(f"Could not classify instruction: '{user_instruction}'. No action taken.")
            return generated_notes, "unknown_instruction", "I'm not sure how to handle that request. Could you rephrase?"

    def _handle_modification_request(self, user_instruction, generated_notes, topics_dict, document_id, document_content_full):
        # Questa è la logica che avevi prima per "modification_request"
        logger.info("Processing as modification request, parallelizing summary updates.")
        updated_notes_session = {}
        notes_to_update_in_db_later = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_topic_id = {}
            for topic_id_str, note_data in generated_notes.items():
                topic_info = topics_dict.get(topic_id_str, {})
                future = executor.submit(
                    self._apply_modification_to_single_summary,
                    topic_id_str,
                    note_data,
                    topic_info,
                    user_instruction,
                    document_id,
                    document_content_full
                )
                future_to_topic_id[future] = topic_id_str
            
            for future in concurrent.futures.as_completed(future_to_topic_id):
                processed_topic_id_str = future_to_topic_id[future]
                try:
                    _, result_data = future.result()
                    updated_content = result_data['content']
                    original_note_data = result_data['original_note_data']
                    
                    updated_notes_session[processed_topic_id_str] = {
                        **original_note_data,
                        'content': updated_content
                    }
                    if result_data['db_update_needed']:
                        notes_to_update_in_db_later.append({
                            'topic_id_str': processed_topic_id_str,
                            'new_content': updated_content,
                            'format': original_note_data.get('format', 'markdown')
                        })
                except Exception as exc:
                    logger.error(f"Topic {processed_topic_id_str} generated an exception during parallel modification: {exc}", exc_info=True)
                    updated_notes_session[processed_topic_id_str] = generated_notes[processed_topic_id_str]
        
        if notes_to_update_in_db_later:
            # ... (logica di aggiornamento DB esistente) ...
            logger.info(f"Updating {len(notes_to_update_in_db_later)} notes in the database.")
            notes_updated_count = 0
            for note_update_data in notes_to_update_in_db_later:
                topic_db = Topic.query.filter_by(document_id=document_id, topic_id=note_update_data['topic_id_str']).first()
                if topic_db:
                    note_db = Note.query.filter_by(topic_id=topic_db.id, format=note_update_data['format']).first()
                    if note_db:
                        note_db.content = note_update_data['new_content']
                        notes_updated_count +=1
                    # ... (else logger.warning) ...
                # ... (else logger.warning) ...
            try:
                if notes_updated_count > 0:
                    self.db.session.commit()
                    logger.info(f"Successfully committed {notes_updated_count} note updates to the database.")
            except Exception as e:
                self.db.session.rollback()
                logger.error(f"Database error while updating notes after parallel processing: {str(e)}", exc_info=True)
        
        return updated_notes_session, "modification_applied"
    
    def _apply_modification_to_single_summary(self, topic_id_str, note_data, topic_info, user_instruction, document_id, document_content_full):
        """
        Applica la modifica a un singolo summary e prepara l'aggiornamento del DB.
        Questa funzione sarà eseguita in parallelo.
        """
        original_content = note_data.get('content', '')
        topic_name = topic_info.get('name', 'this topic')
        topic_description = topic_info.get('description', '') # Contesto specifico del topic

        # Log a warning if the full document content is very large, as it might impact performance/cost
        if len(document_content_full) > 100000: # Example threshold: 100k characters
            logger.warning(f"Full document content for modification prompt is very large ({len(document_content_full)} chars) for topic {topic_name}. This may impact API costs and performance.")


        prompt = f"""You are an AI assistant tasked with refining a summary based on user feedback.
You should consider the full original document content, the specific context snippet for this topic, the existing summary, and the user's specific instruction.

Full Original Document Content (this is the entire text from which all topics were extracted):
---
{document_content_full if document_content_full else "Full document content not available."}
---

Topic Name: "{topic_name}"

Original Context from Document (Specific Source Text Snippet for THIS Topic):
---
{topic_description if topic_description else "No specific context snippet available for this topic."}
---

Existing Summary to Modify:
---
{original_content}
---

User's Instruction for Modification:
---
{user_instruction}
---

Based on the user's instruction, and considering the Full Original Document Content, the Original Context for this Topic, AND the Existing Summary, please provide the new, updated summary.
Output only the final, complete, updated summary text.
"""
        try:
            logger.info(f"Applying modification to topic: {topic_name} (ID: {topic_id_str}) in thread, including full document content and original topic context.")
            ai_response = self.openrouter_client.user_request(
                prompt=prompt,
                model="openai/gpt-3.5-turbo" # O un modello più avanzato se necessario per questa complessità
            )

            if ai_response:
                new_content = ai_response.strip()
                return topic_id_str, {'content': new_content, 'original_note_data': note_data, 'db_update_needed': True}
            else:
                logger.warning(f"AI returned no response for modification of topic {topic_id_str}. Keeping original.")
                return topic_id_str, {'content': original_content, 'original_note_data': note_data, 'db_update_needed': False}
        except Exception as e:
            logger.error(f"Error applying modification to topic {topic_id_str} (Topic: {topic_name}) in thread: {str(e)}", exc_info=True)
            return topic_id_str, {'content': original_content, 'original_note_data': note_data, 'db_update_needed': False}
