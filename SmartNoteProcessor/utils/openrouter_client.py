import os
import requests
import logging
import json
from typing import Dict, Tuple, Any, List
import re
import base64

logger = logging.getLogger(__name__)

class OpenRouterClient:

    model1 = "google/gemini-2.5-flash-preview"         # Per analisi
    model2 = "openai/gpt-4.1-nano"         # Per sintesi
    model3 = "meta-llama/llama-4-maverick:free"         # Per valutazione

    def enhance_topic_info(self, topic_name: str, topic_info: str, output_format: str) -> str:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise ValueError("OpenRouter API key not found. Please set the OPENROUTER_API_KEY environment variable.")

        prompt = f"""
            You are an expert in educational content development. Your task is to improve and reformat the following content about the topic "{topic_name}".

            Instructions:

            - You will receive:
            1. A summary of the topic.
            2. An optional JSON object that lists images and their correlation percentages with various topics.

            - If the JSON is present, you must:
            - Include only the images where the correlation percentage with "{topic_name}" is strictly greater than 75%.
            - Insert these images directly into the relevant paragraphs where the image contextually supports the text.
            - Do NOT place images at the end of the content.
            - For each image inserted, add a short, context-specific description immediately before or after it.

            - Images must be inserted using this format: "{output_format}"
            (Do not alter the image_name. Use it exactly as it appears.)

            - Do not include any JSON or metadata in the final output. The final result must be only the enhanced, well-structured, and image-integrated content.

            - If no JSON object is provided, proceed without inserting any images.

            Content Requirements:

            1. Clearly explain complex concepts.
            2. Use appropriate structure and section headings.
            3. Include examples or analogies to aid understanding.
            4. Ensure all information is accurate, complete, and engaging.
            5. Insert images only when appropriate and relevant to the paragraph topic.

            Final Output Format:

            - Use syntax of the format "{output_format}" with proper headers, bold or italic emphasis, bullet points, and image placement as required.
            - Return only the enhanced content, properly formatted and structured. Do not include any introductory or explanatory text.

            Input content:
            {topic_info}
            """

        try:
            response = requests.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://your-app.com/",
                    "X-Title": "Topic Enhancer"
                },
                json={
                    "model": OpenRouterClient.model2,
                    "messages": [
                        {"role": "user", "content": prompt.strip()}
                    ]
                }
            )

            response.raise_for_status()
            enhanced_info = response.json()["choices"][0]["message"]["content"]

            logger.info(f"Enhanced content for topic: " + enhanced_info)

            return enhanced_info

        except requests.exceptions.RequestException as e:
            logger.error(f"OpenRouter API error: {e}")
            if hasattr(e, 'response') and e.response:
                logger.error(f"Response content: {e.response.text}")
            raise ValueError("Error communicating with OpenRouter API")

        except Exception as e:
            logger.error(f"Error enhancing topic info: {e}")
            raise

    def extract_topics(self, text: str, granularity: int) -> Dict[str, Dict[str, Any]]:
        """
        Extract topics from a given text using OpenRouter.

        Args:
            text: The input document text.
            granularity: The granularity level for topic extraction (0-100).
            model: The model to use via OpenRouter (default: GPT-4).

        Returns:
            A dictionary of extracted topics or an error topic if parsing fails.
        """
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise ValueError("OpenRouter API key not found. Please set the OPENROUTER_API_KEY environment variable.")

        # Optional: replace with a better description function if available
        def _get_granularity_description(granularity_level: int) -> str:
            if granularity_level < 30:
                return "Focus on very high-level themes and general areas of discussion."
            elif granularity_level < 70:
                return "Identify medium-granularity topics that reflect key areas of focus."
            else:
                return "Extract detailed and specific subtopics discussed in the document."

        granularity_description = _get_granularity_description(granularity)

        prompt = f"""
    You are a topic extraction expert. Analyze the following document and extract the main topics at a granularity level of {granularity}/100.
    {granularity_description}

    DOCUMENT TEXT:
    {text[:10000]}

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

    Only respond with the JSON. Do not include any explanations or extra text.
    """

        try:
            response = requests.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://your-app.com/",  # Personalizza questo se necessario
                    "X-Title": "Topic Extractor"
                },
                json={
                    "model": OpenRouterClient.model1,
                    "messages": [
                        {"role": "user", "content": prompt.strip()}
                    ]
                }
            )

            response.raise_for_status()
            response_text = response.json()["choices"][0]["message"]["content"]

            # Parse JSON from response
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
            logger.debug(f"Response was: {response_text if 'response_text' in locals() else 'No response'}")
            return {
                'error_topic': {
                    'name': 'Error Extracting Topics',
                    'description': f"Failed to extract topics from document: {str(e)}"
                }
            }

    def generate_summary(self, document_content: str, topic: str) -> str:

        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OpenRouter API key not found. Please set the OPENROUTER_API_KEY environment variable.")

        # Step 1: Analysis
        analysis_prompt = f"""
            You are an expert analyst. Analyze the following content about "{topic}":

            {document_content}

            Please provide a thorough analysis focusing on:
            1. Key concepts and main ideas
            2. Supporting evidence and details
            3. Contextual relevance
            4. Relationships between different aspects

            Organize your analysis clearly as it will be used to generate a final summary.
            """

        try:
            # Analysis phase
            analysis_response = requests.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://your-app.com/",
                    "X-Title": "Summary Analyzer"
                },
                json={
                    "model": OpenRouterClient.model1,
                    "messages": [
                        {"role": "user", "content": analysis_prompt.strip()}
                    ]
                },
                timeout=30
            )
            analysis_response.raise_for_status()
            analysis_result = analysis_response.json()["choices"][0]["message"]["content"]

            # Step 2: Synthesis
            synthesis_prompt = f"""
                You are an expert summarizer. Create a polished summary based on this analysis of "{topic}":

                Analysis:
                {analysis_result}

                Please produce a final summary that:
                1. Combines key insights coherently
                2. Highlights the most important points
                3. Is well-structured and easy to understand
                4. Uses Markdown formatting for readability
                """
            synthesis_response = requests.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://your-app.com/",
                    "X-Title": "Summary Synthesizer"
                },
                json={
                    "model": OpenRouterClient.model2,
                    "messages": [
                        {"role": "user", "content": synthesis_prompt.strip()}
                    ]
                },
                timeout=30
            )
            synthesis_response.raise_for_status()
            final_summary = synthesis_response.json()["choices"][0]["message"]["content"]

            # Step 3: Evaluation
            evaluation_prompt = f"""
                Evaluate this summary of the topic "{topic}":
                {final_summary}


                Consider that the original data from which the summary is generated is:
                {document_content}

                Provide JUST the percentage score (0-100%) about its quality related to the topic and the original data.
                """
            
            evaluation_response = requests.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://your-app.com/",
                    "X-Title": "Summary Evaluator"
                },
                json={
                    "model": OpenRouterClient.model3,
                    "messages": [
                        {"role": "user", "content": evaluation_prompt.strip()}
                    ]
                },
                timeout=30
            )
            evaluation_response.raise_for_status()
            evaluation_result = evaluation_response.json()["choices"][0]["message"]["content"]

            # Extract accuracy percentage
            accuracy_match = re.search(r'(\d{1,3})\s*%', evaluation_result)
            accuracy = int(accuracy_match.group(1)) if accuracy_match else 0

            if accuracy < 75:
                logger.warning(f"Low summary accuracy ({accuracy}%), regenerating...")
                #return self.generate_summary(document_content, topic)
                return OpenRouterClient.generate_summary(self, document_content, topic)

            # Add evaluation note to summary
            return final_summary

        except requests.exceptions.RequestException as e:
            logger.error(f"OpenRouter API error: {str(e)}")
            if hasattr(e, 'response') and e.response:
                logger.error(f"Response content: {e.response.text}")
            raise ValueError(f"Error communicating with OpenRouter API: {str(e)}")

        except Exception as e:
            logger.error(f"Unexpected error generating summary: {str(e)}")
            raise ValueError(f"Unexpected error: {str(e)}")
        
    def generate_content_with_image(self, prompt, image_path) -> str:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise ValueError("OpenRouter API key not found. Please set the OPENROUTER_API_KEY environment variable.")

        # Leggi e codifica l'immagine in base64
        try:
            with open(image_path, "rb") as image_file:
                image_data = base64.b64encode(image_file.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"Error reading image file: {e}")
            raise ValueError("Failed to read or encode the image file.")

        try:
            response = requests.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://your-app.com/",
                    "X-Title": "Vision Analyzer"
                },
                json={
                    "model": OpenRouterClient.model1,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt.strip()},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}}
                            ]
                        }
                    ]
                }
            )

            response.raise_for_status()
            image_analysis = response.json()["choices"][0]["message"]["content"]
            return image_analysis

        except requests.exceptions.RequestException as e:
            logger.error(f"OpenRouter API error: {e}")
            if hasattr(e, 'response') and e.response:
                logger.error(f"Response content: {e.response.text}")
            raise ValueError("Error communicating with OpenRouter API")

        except Exception as e:
            logger.error(f"Error analyzing image: {e}")
            raise ValueError("Unexpected error during image analysis.")
        
    def generate_percentage(self, prompt, image_path):
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise ValueError("OpenRouter API key not found. Please set the OPENROUTER_API_KEY environment variable.")

        # Leggi e codifica l'immagine in base64
        try:
            with open(image_path, "rb") as image_file:
                image_data = base64.b64encode(image_file.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"Error reading image file: {e}")
            raise ValueError("Failed to read or encode the image file.")

        try:
            response = requests.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://your-app.com/",
                    "X-Title": "Vision Analyzer"
                },
                json={
                    "model": OpenRouterClient.model1,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt.strip()},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}}
                            ]
                        }
                    ]
                }
            )

            response.raise_for_status()
            image_analysis = response.json()["choices"][0]["message"]["content"]
            return image_analysis

        except requests.exceptions.RequestException as e:
            logger.error(f"OpenRouter API error: {e}")
            if hasattr(e, 'response') and e.response:
                logger.error(f"Response content: {e.response.text}")
            raise ValueError("Error communicating with OpenRouter API")

        except Exception as e:
            logger.error(f"Error analyzing image: {e}")
            raise ValueError("Unexpected error during image analysis.")
        
    def merge_topics(self, topic_titles: str) -> str:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise ValueError("OpenRouter API key not found. Please set the OPENROUTER_API_KEY environment variable.")

        prompt = f"""
            Merge in a single topic title the following topic titles: {topic_titles}.
            Return me only the merged topic title, without any other text or explanation.
            """

        try:
            response = requests.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://your-app.com/",
                    "X-Title": "Topic Merger"
                },
                json={
                    "model": OpenRouterClient.model2,
                    "messages": [
                        {"role": "user", "content": prompt.strip()}
                    ]
                }
            )

            response.raise_for_status()
            merged_info = response.json()["choices"][0]["message"]["content"]
            return merged_info

        except requests.exceptions.RequestException as e:
            logger.error(f"OpenRouter API error: {e}")
            if hasattr(e, 'response') and e.response:
                logger.error(f"Response content: {e.response.text}")
            raise ValueError("Error communicating with OpenRouter API")

        except Exception as e:
            logger.error(f"Error merging topic info: {e}")
            raise

    def user_request(self, prompt: str, model: str = None) -> str:
        """
        Genera testo basato su un prompt utilizzando un modello specificato.
        """
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            logger.error("OpenRouter API key not found.")
            raise ValueError("OpenRouter API key not found. Please set the OPENROUTER_API_KEY environment variable.")

        selected_model = model if model else OpenRouterClient.model_generic

        try:
            logger.debug(f"Sending generic text generation request to OpenRouter. Model: {selected_model}. Prompt: {prompt[:200]}...")
            response = requests.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": os.environ.get("OPENROUTER_REFERRER", "http://localhost:5000"), # Usa variabile d'ambiente o default
                    "X-Title": os.environ.get("OPENROUTER_X_TITLE", "SmartNoteProcessor") # Usa variabile d'ambiente o default
                },
                json={
                    "model": selected_model,
                    "messages": [
                        {"role": "user", "content": prompt.strip()}
                    ]
                },
                timeout=60  # Aumentato timeout per richieste potenzialmente più lunghe
            )
            response.raise_for_status()
            response_json = response.json()
            content = response_json.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                logger.warning("OpenRouter returned an empty content for generic text generation.")
            return content.strip()

        except requests.exceptions.RequestException as e:
            logger.error(f"OpenRouter API error during generic text generation: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}, Response content: {e.response.text}")
            raise ValueError(f"Error communicating with OpenRouter API: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error during generic text generation: {str(e)}", exc_info=True)
            raise ValueError(f"Unexpected error during generic text generation: {str(e)}")

    def classify_instruction(self, prompt: str, model: str = None) -> str:
        """
        Classifica un'istruzione dell'utente utilizzando un modello AI.
        """
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            logger.error("OpenRouter API key not found.")
            raise ValueError("OpenRouter API key not found. Please set the OPENROUTER_API_KEY environment variable.")

        selected_model = self.model2

        try:
            logger.debug(f"Sending classification request to OpenRouter. Model: {selected_model}. Prompt: {prompt[:200]}...")
            response = requests.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": os.environ.get("OPENROUTER_REFERRER", "http://localhost:5000"), 
                    "X-Title": os.environ.get("OPENROUTER_X_TITLE", "SmartNoteProcessor_Classification") 
                },
                json={
                    "model": selected_model,
                    "messages": [
                        {"role": "user", "content": prompt.strip()}
                    ],
                    "max_tokens": 10 # La classificazione dovrebbe essere breve
                },
                timeout=30 
            )
            response.raise_for_status()
            response_json = response.json()
            content = response_json.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                logger.warning("OpenRouter returned an empty content for classification.")
            return content.strip()

        except requests.exceptions.RequestException as e:
            logger.error(f"OpenRouter API error during classification: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}, Response content: {e.response.text}")
            raise ValueError(f"Error communicating with OpenRouter API for classification: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error during classification: {str(e)}", exc_info=True)
            raise ValueError(f"Unexpected error during classification: {str(e)}")