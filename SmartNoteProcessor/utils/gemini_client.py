import os
import logging
import time
import requests
from typing import Dict, Any, Optional
import json

# Configure logging
logger = logging.getLogger(__name__)

class GeminiClient:
    """
    Client for interacting with the Gemini model via OpenRouter.
    Handles API calls, rate limiting, and error handling.
    """
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the Gemini client using OpenRouter.
        
        Args:
            api_key: OpenRouter API key (optional, can be set via env var)
        
        Raises:
            ValueError: If API key is not provided and not found in environment
        """
        # Get API key from parameter or environment variable
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        
        if not self.api_key:
            logger.error("No OpenRouter API key found")
            raise ValueError(
                "OpenRouter API key is required. Either pass it to the constructor "
                "or set OPENROUTER_API_KEY environment variable."
            )
        
        logger.info("Gemini client configured to use OpenRouter")
        
        # Rate limiting parameters
        self.requests_per_minute = 10  # Conservative limit
        self.last_request_time = 0
        
        # Default model name
        self.model_name = "google/gemini-2.5-flash-preview"
    
    def generate_content(self, prompt: str, retry_count: int = 1) -> str:
        """
        Generate content using Gemini via OpenRouter.
        
        Args:
            prompt: The text prompt to send to Gemini
            retry_count: Number of retries on failure
            
        Returns:
            Generated text response
            
        Raises:
            Exception: If generation fails after all retries
        """
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://your-app.com/",
            "X-Title": "Gemini Client via OpenRouter"
        }
        
        for attempt in range(retry_count + 1):
            try:
                # Prepare the payload
                payload = {
                    "model": self.model_name,
                    "messages": [
                        {"role": "user", "content": prompt}
                    ]
                }
                
                # Log the payload for debugging
                logger.debug(f"Payload being sent: {json.dumps(payload, indent=2)}")
                
                # Send the request to OpenRouter
                response = requests.post(url, headers=headers, json=payload)
                
                # Log the response content for debugging
                logger.debug(f"Response content: {response.text}")
                response.raise_for_status()
                
                # Parse the response
                response_data = response.json()
                return response_data["choices"][0]["message"]["content"]
            
            except requests.exceptions.RequestException as e:
                logger.error(f"OpenRouter API error (attempt {attempt+1}/{retry_count+1}): {str(e)}")
                
                if attempt < retry_count:
                    wait_time = 1
                    logger.info(f"Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    error_msg = f"Failed to generate content: {str(e)}"
                    logger.error(error_msg)
                    return f"Error: {error_msg}"
    
    def extract_topics(self, text: str, granularity: int) -> Dict[str, Dict[str, Any]]:
        """
        Extract topics from text with specified granularity level using OpenRouter.
        
        Args:
            text: Document text to extract topics from
            granularity: Integer from 0-100 indicating granularity level
            
        Returns:
            Dictionary of topics with their details
        """
        granularity_description = self._get_granularity_description(granularity)
        
        prompt = f"""
        Analyze the following document and extract the main topics at a granularity level of {granularity}/100.
        {granularity_description}
        
        DOCUMENT TEXT:
        {text[:10000]}  # Limit text to avoid token limits
        
        Extract topics and return them in the following JSON format:
        {{
            "topics": [
                {{
                    "id": "unique_id_1",
                    "name": "Topic Name 1",
                    "description": "Brief description of the topic"
                }},
                ...
            ]
        }}
        
        Only respond with the JSON. Do not include any explanations or additional text before or after the JSON.
        """
        
        response_text = self.generate_content(prompt)
        
        try:
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            
            if json_start == -1 or json_end == 0:
                raise ValueError("No JSON found in response")
                
            json_str = response_text[json_start:json_end]
            response_json = json.loads(json_str)
            
            topics_dict = {}
            for topic in response_json.get('topics', []):
                topic_id = topic.get('id', f"topic_{len(topics_dict)}")
                topics_dict[topic_id] = {
                    'name': topic.get('name', 'Unnamed Topic'),
                    'description': topic.get('description', '')
                }
            
            return topics_dict
        except Exception as e:
            logger.error(f"Error parsing topics JSON: {str(e)}")
            logger.debug(f"Response was: {response_text}")
            return {
                'error_topic': {
                    'name': 'Error Extracting Topics',
                    'description': f"Failed to extract topics from document: {str(e)}"
                }
            }
    
    def _get_granularity_description(self, granularity: int) -> str:
        """
        Get a description of the requested granularity level.
        
        Args:
            granularity: Integer from 0-100
            
        Returns:
            Text description of granularity for the prompt
        """
        if granularity < 20:
            return "Extract only the broadest, most general macro-topics (very few top-level topics)."
        elif granularity < 40:
            return "Extract general macro-topics (a small number of broad topics)."
        elif granularity < 60:
            return "Extract a balanced mix of general topics and some specific sub-topics."
        elif granularity < 80:
            return "Extract more specific sub-topics with moderate detail."
        else:
            return "Extract highly specific, detailed micro-topics (many fine-grained topics)."
        
    def enhance_topic_info(self, topic_name: str, topic_info: str) -> str:
        """
        Enhance topic information with additional context and explanations.
        
        Args:
            topic_name: Name of the topic
            topic_info: Raw information extracted for the topic
            
        Returns:
            Enhanced, well-structured information about the topic
        """
        prompt = f"""
        You are an educational content expert. You need to enhance the following information about the topic "{topic_name}".
        
        Original information:
        {topic_info}
        
        Please enhance this information by:
        1. Adding clear explanations for complex concepts
        2. Organizing the content with appropriate headings and structure
        3. Adding examples or analogies where helpful
        4. Ensuring the information is accurate and complete
        
        The enhanced content should be well-structured and easy to understand for a student studying this topic.
        Format the content using Markdown syntax with appropriate headers, lists, and emphasis.
        """
        
        enhanced_info = self.generate_content(prompt)
        return enhanced_info

    def generate_content_with_image(self, prompt: str, image_data: str, retry_count: int = 1) -> str:
        """
        Generate content using Gemini Vision model with both text prompt and image.
        
        Args:
            prompt: The text prompt to send to Gemini
            image_data: Base64-encoded image data
            retry_count: Number of retries on failure
            
        Returns:
            Generated text response
            
        Raises:
            Exception: If generation fails after all retries
        """
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://your-app.com/",
            "X-Title": "Gemini Vision Client via OpenRouter"
        }
        
        for attempt in range(retry_count + 1):
            try:
                # Prepare the payload with text and image
                payload = {
                    "model": "google/gemini-pro-vision",  # Replace with the correct vision model ID
                    "messages": [
                        {"role": "user", "content": prompt}
                    ],
                    "files": [
                        {
                            "name": "image.jpg",
                            "type": "image/jpeg",
                            "content": image_data
                        }
                    ]
                }
                
                # Send the request to OpenRouter
                response = requests.post(url, headers=headers, json=payload)
                response.raise_for_status()
                
                # Parse the response
                response_data = response.json()
                return response_data["choices"][0]["message"]["content"]
            
            except requests.exceptions.RequestException as e:
                logger.error(f"Gemini Vision API error (attempt {attempt+1}/{retry_count+1}): {str(e)}")
                
                if attempt < retry_count:
                    wait_time = 1
                    logger.info(f"Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    error_msg = f"Failed to analyze image: {str(e)}"
                    logger.error(error_msg)
                    return f"Error analyzing image: {error_msg}"