import os
import logging
import base64
from typing import Dict, List, Any, Optional, Tuple
from io import BytesIO
from PIL import Image

# Set up logging
logger = logging.getLogger(__name__)

class ImageAnalyzer:
    """
    Analyzes images using Google's Gemini Vision model to extract relevant information
    related to specific topics.
    """
    
    def __init__(self, gemini_client):
        """
        Initialize the image analyzer.
        
        Args:
            gemini_client: An instance of GeminiClient for LLM operations
        """
        self.gemini_client = gemini_client
    
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
            encoded_image = self._encode_image(image_path)
            if not encoded_image:
                return {}
                
            # Prepare list of topic names for the prompt
            topic_names = [topic_data['name'] for topic_id, topic_data in topics.items()]
            topic_names_str = ", ".join([f'"{name}"' for name in topic_names])
            
            # Create prompt for Gemini Vision
            # --- MODIFICA: Prompt più specifico ---
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
            # --- FINE MODIFICA ---

            # Call Gemini Vision API with image and prompt
            vision_response = self.gemini_client.generate_content_with_image(prompt, encoded_image)
            
            # Parse response to extract topic-relevant information
            info_by_topic = self._parse_vision_response(vision_response, topics)
            
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
            import json
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
                info_by_topic = self.extract_info_from_image(image_path, topics)
                
                # Add information to result
                for topic_id, info in info_by_topic.items():
                    if info:  # Only add non-empty information
                        image_info_by_topic[topic_id][image_file] = info
            
            return image_info_by_topic
            
        except Exception as e:
            logger.error(f"Error analyzing images in folder: {str(e)}")
            return image_info_by_topic