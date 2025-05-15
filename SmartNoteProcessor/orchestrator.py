import os
import logging
from datetime import datetime
from models import Document, Topic, Note, ImageAnalysis # Ensure models are correctly imported

logger = logging.getLogger(__name__)

class SmartNotesOrchestrator:
    def __init__(self, document_processor, topic_extractor, gemini_client, format_converter, image_analyzer, db, app_config):
        self.document_processor = document_processor
        self.topic_extractor = topic_extractor
        self.gemini_client = gemini_client
        self.format_converter = format_converter
        self.image_analyzer = image_analyzer # This component will need a method to process images for a topic
        self.db = db
        self.app_config = app_config

    def process_and_generate(self, primary_document_id, combined_content, topics_dict, output_format, process_images_flag, document_upload_folder_path):
        """
        Orchestrates the generation of notes for a given document and its topics.
        Assumes topics_dict (from topic_extractor) is already available.
        `document_upload_folder_path` is the root folder for the specific document's assets.
        """
        generated_notes = {}
        successfully_processed_count = 0
        errors_encountered = []

        db_document = Document.query.get(primary_document_id)
        if not db_document:
            errors_encountered.append(f"Document with ID {primary_document_id} not found.")
            logger.error(f"Orchestrator: Document with ID {primary_document_id} not found.")
            return {}, errors_encountered, 0

        db_topics_for_doc = Topic.query.filter_by(document_id=primary_document_id).all()
        # Map model-generated topic_id (string) to the database Topic object
        topic_map_by_model_id = {t.topic_id: t for t in db_topics_for_doc}

        logger.info(f"Orchestrator: Starting note generation for document ID {primary_document_id}, {len(topics_dict)} topics. Format: {output_format}, Images: {process_images_flag}")

        for topic_id_str, topic_data in topics_dict.items():
            topic_name = topic_data.get('name', f"Topic {topic_id_str}")
            logger.info(f"Orchestrator: Processing topic '{topic_name}' (model ID: {topic_id_str})")

            db_topic_obj = topic_map_by_model_id.get(topic_id_str)
            if not db_topic_obj:
                logger.warning(f"Orchestrator: DB Topic object not found for model topic_id {topic_id_str}. Skipping note generation for '{topic_name}'.")
                errors_encountered.append(f"Could not find topic '{topic_name}' in the database to associate the note.")
                continue

            # Check for existing note in DB for this topic and format
            existing_db_note = Note.query.filter_by(
                topic_id=db_topic_obj.id, # Use the DB Topic's primary key
                format=output_format
            ).first()

            if existing_db_note:
                logger.info(f"Orchestrator: Using existing note from DB for topic: {topic_name}")
                generated_notes[topic_id_str] = {
                    'name': topic_name,
                    'content': existing_db_note.content,
                    'format': output_format
                }
                successfully_processed_count += 1
                continue

            # Generate new note if not found in DB
            try:
                topic_info = self.document_processor.extract_topic_information(
                    combined_content, topic_name, self.gemini_client
                )
                # Handle API errors (e.g., quota)
                if isinstance(topic_info, str) and "Error:" in topic_info:
                    logger.error(f"Orchestrator: Error extracting info for '{topic_name}': {topic_info}")
                    errors_encountered.append(f"Error extracting info for '{topic_name}': {topic_info}")
                    continue

                enhanced_info = self.gemini_client.enhance_topic_info(topic_name, topic_info)
                if isinstance(enhanced_info, str) and "Error:" in enhanced_info:
                    logger.error(f"Orchestrator: Error enhancing info for '{topic_name}': {enhanced_info}")
                    errors_encountered.append(f"Error enhancing info for '{topic_name}': {enhanced_info}")
                    continue
                
                image_analysis_content = ""
                if process_images_flag and document_upload_folder_path:
                    images_subfolder = os.path.join(document_upload_folder_path, 'images')
                    if os.path.exists(images_subfolder) and os.path.isdir(images_subfolder):
                        # NOTE: You'll need to implement/ensure ImageAnalyzer has a method like 'analyze_images_and_get_summary'
                        # This method should handle finding images, calling Gemini Vision, saving ImageAnalysis records,
                        # and returning a formatted string summary of analyses for the current topic.
                        # Example: image_analysis_content = self.image_analyzer.analyze_images_and_get_summary(db_topic_obj.id, images_subfolder, self.db.session)
                        logger.info(f"Orchestrator: Image processing to be implemented via ImageAnalyzer for topic '{topic_name}'.")
                        # Placeholder for actual image analysis call and content aggregation
                    else:
                        logger.info(f"Orchestrator: Images subfolder not found or not a directory: {images_subfolder}")


                final_topic_content = enhanced_info + image_analysis_content # Append image analysis here
                formatted_content = self.format_converter.convert(
                    topic_name, final_topic_content, output_format
                )

                new_note = Note(
                    content=formatted_content,
                    format=output_format,
                    topic_id=db_topic_obj.id # Link to the DB Topic's primary key
                )
                self.db.session.add(new_note)
                # Consider batch committing outside the loop for performance, or commit here if preferred
                self.db.session.commit() 

                generated_notes[topic_id_str] = {
                    'name': topic_name,
                    'content': formatted_content,
                    'format': output_format
                }
                successfully_processed_count += 1

            except Exception as e:
                self.db.session.rollback()
                logger.error(f"Orchestrator: Error processing topic {topic_name} ({topic_id_str}): {str(e)}", exc_info=True)
                errors_encountered.append(f"Error processing topic '{topic_name}': {str(e)}")
        
        # Hyperlinking (after all notes are generated/retrieved for the current run)
        if generated_notes:
            logger.info("Orchestrator: Adding hyperlinks to notes.")
            try:
                # Ensure all notes for hyperlinking are available (from current run or DB)
                all_notes_for_hyperlinking = {}
                for t_id_str_link, t_data_link in topics_dict.items():
                    if t_id_str_link in generated_notes: # Notes generated/retrieved in this run
                        all_notes_for_hyperlinking[t_id_str_link] = generated_notes[t_id_str_link]
                    else: # If not in current run, try to fetch from DB if it's a known topic
                        db_topic_for_link = topic_map_by_model_id.get(t_id_str_link)
                        if db_topic_for_link:
                            db_note_for_link = Note.query.filter_by(topic_id=db_topic_for_link.id, format=output_format).first()
                            if db_note_for_link:
                                 all_notes_for_hyperlinking[t_id_str_link] = {'name': t_data_link['name'], 'content': db_note_for_link.content, 'format': output_format}
                
                notes_with_links = self.format_converter.add_hyperlinks(
                    all_notes_for_hyperlinking, topics_dict, output_format
                )

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
                self.db.session.commit()
                generated_notes = notes_with_links # Return notes with hyperlinks
                logger.info("Orchestrator: Hyperlinks added and notes updated in DB.")

            except Exception as link_err:
                self.db.session.rollback()
                logger.error(f"Orchestrator: Error during hyperlinking: {str(link_err)}", exc_info=True)
                errors_encountered.append(f"Error adding internal links: {str(link_err)}")

        # Generate Introductory Markdown File (if applicable)
        if output_format == 'markdown' and generated_notes:
            intro_content = "# Table of Contents\n\nThis document provides an overview and links to all generated notes:\n\n"
            # Sort topics by name for the index, excluding the index itself
            sorted_notes_for_index = sorted(
                [(tid, data) for tid, data in generated_notes.items() if tid != "000_index_introduction_page"],
                key=lambda item: item[1].get('name', '')
            )
            for topic_id_str_idx, note_data_idx in sorted_notes_for_index:
                note_name_idx = note_data_idx.get('name', f"Topic {topic_id_str_idx}")
                if note_data_idx.get('format') == 'markdown':
                    filename_idx = f"{note_name_idx.replace(' ', '_').replace('/', '_')}.md"
                    intro_content += f"- [{note_name_idx}](./{filename_idx})\n"
            
            generated_notes["000_index_introduction_page"] = {
                'name': "Introduction", 
                'content': intro_content,
                'format': 'markdown' 
            }
            logger.info("Orchestrator: Introductory Markdown file content generated.")

        return generated_notes, errors_encountered, successfully_processed_count