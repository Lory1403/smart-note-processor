import logging
from typing import Dict, List, Any, Optional
from utils.openrouter_client import OpenRouterClient

logger = logging.getLogger(__name__)

class MergeTopics:
    
    def __init__(self, openrouter_client):
        self.openrouter_client = openrouter_client
    
    def merge_topics(self, topic_titles) -> str:
        try:
            # Use Gemini to extract topics
            topic = OpenRouterClient.merge_topics(self, topic_titles)
            
            # Log the number of topics found
            logger.info(f"Merged topics{topic_titles} in {topic}")
            
            return topic
        except Exception as e:
            logger.error(f"Error in topic merging: {str(e)}")
            # Return a single error topic in case of failure
            return {
                'error': {
                    'name': 'Error in Topic Merging',
                    'description': f"Failed to merge topics: {str(e)}"
                }
            }