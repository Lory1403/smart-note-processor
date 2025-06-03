import logging
from utils.openrouter_client import OpenRouterClient

logger = logging.getLogger(__name__)

class SummaryExtractor:
    """
    Extracts topics from document content with adjustable granularity.
    Uses Gemini LLM for intelligent topic extraction.
    """
    
    def __init__(self, openrouter_client):
        """
        Initialize the topic extractor.
        
        Args:
            gemini_client: An instance of GeminiClient for LLM operations
        """
        self.openrouter_client = openrouter_client
    
    def extract_resumes(self, document_content: str, topic: str) -> str:
        try:
            
            resume = OpenRouterClient.generate_summary(self, document_content, topic)
            
            logger.info(f"Created resume for {topic}")
            
            return resume
        except Exception as e:
            logger.error(f"Error in resume generation: {str(e)}")
            return {
                'error': {
                    'name': 'Error in Resume Generation',
                    'description': f"Failed to extract topics: {str(e)}"
                }
            }
    