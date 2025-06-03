import logging
from typing import Dict, List, Any, Optional
from utils.openrouter_client import OpenRouterClient

logger = logging.getLogger(__name__)

class ResumeesEnhancer:
    def __init__(self, openrouter_client):
        self.openrouter_client = openrouter_client
    
    def enhance_resumes(self, topic_name: str, resumee: str, output_format: str) -> str:
        try:
            # Use Gemini to extract topics
            enhanced = OpenRouterClient.enhance_topic_info(self, topic_name, resumee, output_format)
            
            # Log the number of topics found
            logger.info(f"Enhanced resume for {topic_name}")
            
            return enhanced
        except Exception as e:
            error_message = f"Error in resume enhancing: {str(e)}"
            logger.error(error_message)
            # Return a string error message instead of a dictionary
            return f"Error: {error_message}"
        