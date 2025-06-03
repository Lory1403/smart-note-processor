import os
import logging
import base64
from typing import Dict, List, Any, Optional, Tuple
from io import BytesIO
from PIL import Image
from models import ImageAnalysis, Topic
import json # Import json at the module level

# Set up logging
logger = logging.getLogger(__name__)

class ImageAnalyzer:
    """
    Analyzes images using Google's Gemini Vision model to extract relevant information
    related to specific topics.
    """
    
    def __init__(self, openrouter_client):
        self.openrouter_client = openrouter_client
    
    def extract_info_from_image(self, image_path: str, topics: Dict[str, Any]) -> Dict[str, str]:
        """
        Extract information from an image that's relevant to the specified topics.
        
        Args:
            image_path: Path to the image file
            topics: Dictionary of topics extracted from the document
            
        Returns:
            Dictionary mapping topic IDs to extracted information from the image
        """
        try:
            # Encode image for Gemini Vision model
            image_analyzer = ImageAnalyzer(self.openrouter_client)
                
            # Prepare list of topic names for the prompt
            topic_names = [topic_data['name'] for topic_id, topic_data in topics.items()]
            topic_names_str = ", ".join([f'"{name}"' for name in topic_names])
            
            # Create prompt for Gemini Vision
            prompt = f"""
            Analyze this image carefully. Determine if it illustrates or provides significant visual information for any of the following topics:
            {topic_names_str}, or any related subtopics treated in the document.

            For EACH topic the image is STRONGLY and DIRECTLY relevant to, provide a concise description (1-3 sentences) explaining HOW the image illustrates that specific topic. Focus on the visual elements (diagrams, charts, scenes, objects) and their connection to the topic.

            IGNORE topics where the image's relevance is weak, indirect, or purely based on text shown within the image itself unless that text is part of a diagram/chart relevant to the topic. Do not describe images that are just blocks of text.

            Format your response STRICTLY as a JSON object like this:
            {{
                "topic_name_directly_illustrated_1": "Description of how the image illustrates this topic...",
                "topic_name_directly_illustrated_2": "Description of how the image illustrates this topic..."
            }}

            If the image is not directly relevant to ANY of the listed topics, respond with an empty JSON object: {{}}
            """

            vision_response = self.openrouter_client.generate_content_with_image(prompt, image_path)
            
            # Parse response to extract topic-relevant information
            image_analyzer = ImageAnalyzer(self.openrouter_client)
            info_by_topic = image_analyzer._parse_vision_response(vision_response, topics)
            
            return info_by_topic
            
        except Exception as e:
            logger.error(f"Error analyzing image: {str(e)}")
            return {}
    
    def _encode_image(self, image_path: str) -> Optional[str]:
        """
        Encode an image to base64 for the Gemini Vision API.
        
        Args:
            image_path: Path to the image file
            
        Returns:
            Base64 encoded image or None if encoding fails
        """
        try:
            # Open and resize image if needed
            with Image.open(image_path) as img:
                # Resize large images to reduce API costs
                max_size = 1024
                if max(img.width, img.height) > max_size:
                    if img.width > img.height:
                        new_width = max_size
                        new_height = int(img.height * (max_size / img.width))
                    else:
                        new_height = max_size
                        new_width = int(img.width * (max_size / img.height))
                    img = img.resize((new_width, new_height))
                
                # Convert to RGB if needed
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
                # Save to BytesIO and encode
                buffer = BytesIO()
                img.save(buffer, format="JPEG")
                return base64.b64encode(buffer.getvalue()).decode('utf-8')
                
        except Exception as e:
            logger.error(f"Error encoding image: {str(e)}")
            return None
    
    def _parse_vision_response(self, response: str, topics: Dict[str, Any]) -> Dict[str, str]:
        """
        Parse the response from Gemini Vision API to extract topic-relevant information.
        
        Args:
            response: Response text from Gemini Vision API
            topics: Dictionary of topics extracted from the document
            
        Returns:
            Dictionary mapping topic IDs to extracted information
        """
        info_by_topic = {}
        
        try:
            # Try to find a JSON object in the response
            import re
            
            # Find JSON-like structure in the response, handling potential markdown backticks
            response_cleaned = response.strip().strip('`') # Remove potential markdown code block markers
            if response_cleaned.startswith("json"):
                 response_cleaned = response_cleaned[4:].strip() # Remove potential 'json' prefix

            json_match = re.search(r'\{.*\}', response_cleaned, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
                # --- AGGIUNTA: Gestione JSON vuoto o quasi vuoto ---
                if len(json_str) <= 2: # Considera "{}" come vuoto
                    logger.info("Vision response was an empty JSON object, indicating no relevant topics found.")
                    return {} # Restituisce dizionario vuoto
                # --- FINE AGGIUNTA ---
                parsed_data = json.loads(json_str)
                
                # Match topic names to topic IDs
                name_to_id = {topic_data['name']: topic_id for topic_id, topic_data in topics.items()}
                
                # Map information to topic IDs
                for topic_name, info in parsed_data.items():
                    if topic_name in name_to_id and isinstance(info, str) and info.strip(): # Assicurati che l'info sia una stringa non vuota
                        info_by_topic[name_to_id[topic_name]] = info.strip()
                    else:
                         logger.warning(f"Topic name '{topic_name}' from vision response not found in provided topics or info was invalid.")

            # --- MODIFICA: Gestione risposta non JSON ---
            elif "no relevant information found" in response.lower() or response.strip() == '{}':
                 logger.info("Vision response indicated no relevant topics found (non-JSON).")
                 return {} # Restituisce dizionario vuoto se la risposta testuale indica irrilevanza
            else:
                 logger.warning(f"Could not find valid JSON in vision response: {response}")
            # --- FINE MODIFICA ---

        except json.JSONDecodeError as json_err:
            logger.error(f"Error parsing vision response JSON: {json_err}. Response: {response}")
        except Exception as e:
            logger.error(f"Unexpected error parsing vision response: {str(e)}. Response: {response}")
        
        return info_by_topic
    
    def analyze_images_for_topics(self, images_folder: str, topics: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
        """
        Analyze multiple images in a folder and extract information relevant to topics.
        
        Args:
            images_folder: Path to folder containing images
            topics: Dictionary of topics extracted from the document
            
        Returns:
            Dictionary mapping topic IDs to dictionaries of image information
            {
                'topic_id_1': {
                    'image1.jpg': 'Information about topic from image1',
                    'image2.jpg': 'Information about topic from image2',
                },
                'topic_id_2': {
                    'image1.jpg': 'Information about topic from image1',
                }
            }
        """
        image_info_by_topic = {topic_id: {} for topic_id in topics}
        
        try:
            # Get list of image files
            image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp'}
            image_files = [
                f for f in os.listdir(images_folder) 
                if os.path.isfile(os.path.join(images_folder, f)) and 
                os.path.splitext(f)[1].lower() in image_extensions
            ]
            
            # Process each image
            for image_file in image_files:
                image_path = os.path.join(images_folder, image_file)
                
                # Extract information from image
                image_analyzer = ImageAnalyzer(self.openrouter_client)
                info_by_topic = image_analyzer.extract_info_from_image(image_path, topics)
                
                # Add information to result
                for topic_id, info in info_by_topic.items():
                    if info:  # Only add non-empty information
                        image_info_by_topic[topic_id][image_file] = info
            
            return image_info_by_topic
            
        except Exception as e:
            logger.error(f"Error analyzing images in folder: {str(e)}")
            return image_info_by_topic
        
    def get_topic_correlation(self, image_path: str, topics: Dict[str, Any]) -> str:
        try:
            # Encode image for Gemini Vision model
            image_analyzer = ImageAnalyzer(self.openrouter_client)
                
            # Prepare list of topic names for the prompt
            topic_names = [topic_data['name'] for topic_id, topic_data in topics.items()]
            topic_names_str = ", ".join([f'"{name}"' for name in topic_names])
            
            # Create prompt for Gemini Vision
            prompt = f"""
            Please analyze this image carefully. Determine whether it illustrates or provides meaningful visual information for any of the following topics: "{topic_names_str} "
            I ask you to calculate a percentage of correlation between the topic and the image

            I ask you to generate a response in STRICTLY JSON format structured as follows:
            {{
            "topic_name_1": "Percentage...",
            "topic_name_2": "Percentage..."
            }}

            If the image is not directly relevant to ANY of the topics listed, respond with an empty JSON object: {{}}
            If the image contains only text, please ignore it and respond with an empty JSON object: {{}}
            """

            vision_response = self.openrouter_client.generate_percentage(prompt, image_path)
            
            # Parse response to extract topic-relevant information
            image_analyzer = ImageAnalyzer(self.openrouter_client)
            info_by_topic = image_analyzer._parse_vision_response(vision_response, topics)
            
            return info_by_topic
        
        except Exception as e:
            logger.error(f"Error analyzing image: {str(e)}")
            return {}

    def analyze_images_and_get_summary(self, topics: Dict[str, Any], images_folder: str, db_session) -> Tuple[str, List[ImageAnalysis]]:
        """
        Analizza tutte le immagini nella cartella fornita rispetto ai topic e restituisce un riassunto testuale
        e una lista di oggetti ImageAnalysis transienti.
        """
        summary_lines = []
        new_image_analysis_objects = [] # Lista per raccogliere i nuovi oggetti ImageAnalysis

        if not os.path.isdir(images_folder):
            return "", new_image_analysis_objects

        image_files = [f for f in os.listdir(images_folder) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif'))]
        if not image_files:
            return "", new_image_analysis_objects

        all_results_json_parts = {}

        for img_filename in image_files:
            img_path = os.path.join(images_folder, img_filename)
            try:
                # Assicurati che 'get_topic_correlation' restituisca un JSON string valido o None/{}
                analysis_result_from_correlation = self.get_topic_correlation(img_path, topics)
                
                analysis_data = {}
                # Controlla se il risultato è una stringa prima di tentare il parsing
                if isinstance(analysis_result_from_correlation, str):
                    if analysis_result_from_correlation.strip() and analysis_result_from_correlation.strip() != "{}":
                        try:
                            analysis_data = json.loads(analysis_result_from_correlation)
                            all_results_json_parts[img_filename] = analysis_data # Store parsed JSON
                        except json.JSONDecodeError:
                            logger.error(f"Failed to parse JSON from get_topic_correlation for {img_filename}: {analysis_result_from_correlation}")
                            summary_lines.append(f"- **{img_filename}**: Errore nell'analisi (JSON non valido)")
                            continue # Skip this image if JSON is invalid
                elif isinstance(analysis_result_from_correlation, dict):
                    # Se è già un dizionario (es. {} da un errore in get_topic_correlation), usalo direttamente
                    analysis_data = analysis_result_from_correlation
                    if analysis_data: # Se non è vuoto
                         all_results_json_parts[img_filename] = analysis_data


                if analysis_data: # Proceed if we have valid analysis data
                    # Questa logica per 'topics_db_objs' deve essere corretta se 'analysis_data.keys()'
                    # sono nomi di topic e non ID di Topic del DB.
                    # Per ora, assumiamo che sia gestita correttamente o che venga passata una mappatura.
                    # Temporaneamente, per evitare errori, la lasceremo vuota se la logica di mapping non è chiara.
                    # TODO: Rivedere la logica di mapping tra i risultati dell'analisi (nomi topic?) e gli ID dei Topic del DB.
                    
                    # Placeholder: questa parte richiede una logica corretta per mappare i risultati ai Topic del DB.
                    # Per ora, creiamo ImageAnalysis senza la relazione 'topics' se la logica di mapping non è chiara.
                    relevant_topic_db_objects = []
                    # Esempio di come potrebbe essere (richiede che `topics` contenga info per mappare nomi a ID DB):
                    # topic_name_to_db_id_map = {data['name']: topic_db_id for topic_db_id, data in topics_input_to_correlation.items()}
                    # relevant_topic_db_ids = []
                    # for topic_name, percentage_str in analysis_data.items():
                    #     if float(percentage_str.rstrip('%')) > 75 and topic_name in topic_name_to_db_id_map:
                    #         relevant_topic_db_ids.append(topic_name_to_db_id_map[topic_name])
                    # if relevant_topic_db_ids:
                    #    relevant_topic_db_objects = db_session.query(Topic).filter(Topic.id.in_(relevant_topic_db_ids)).all()

                    image_analysis = ImageAnalysis(
                        filename=img_filename,
                        path=img_path, # Salva il percorso completo o relativo come necessario
                        analysis_result=json.dumps(analysis_data), # Salva il JSON come stringa
                        # topics=relevant_topic_db_objects # Assegna i Topic ORM objects
                    )
                    new_image_analysis_objects.append(image_analysis) # Aggiungi l'oggetto transient alla lista

                    rel_img_path = os.path.join(os.path.basename(os.path.dirname(img_path)), img_filename) # Path relativo per Markdown/HTML
                    summary_lines.append(
                        f"- ![]({rel_img_path})\n  **{img_filename}**: {json.dumps(analysis_data)}"
                    )
            except Exception as e:
                logger.error(f"Errore durante l'analisi dell'immagine {img_filename}: {e}", exc_info=True)
                summary_lines.append(f"- **{img_filename}**: Errore durante l'analisi ({e})")

        # Rimosso: db_session.commit()

        final_summary_text = ""
        if summary_lines:
            final_summary_text = "\n\n### Analisi delle immagini correlate\n" + "\n".join(summary_lines) + "\n"
        
        return final_summary_text, new_image_analysis_objects