import os
import requests
import logging
import json
from typing import Dict, Tuple, Any, List

logger = logging.getLogger(__name__)

def generate_summary_with_openrouter(
    arguments: Dict[str, Any], 
    content_text: str,
    file_contents: Dict[str, str] = {},
    model1: str = "google/gemini-2.5-flash-preview",
    model2: str = "meta-llama/llama-guard-4-12b",
    model3: str = "qwen/qwen3-235b-a22b:free",
    generate_markdown: bool = True
) -> Tuple[str, Dict[str, Any]]:
    """
    Generate a summary of the provided arguments using three models via OpenRouter:
    analysis, synthesis, and evaluation.
    
    Args:
        arguments: Dictionary of arguments to summarize
        content_text: Additional content text for context
        file_contents: Dictionary mapping filenames to their contents
        model1: ID of the first model to use for analysis
        model2: ID of the second model to use for synthesis
        model3: ID of the third model to use for evaluation
        generate_markdown: Whether to format the output as markdown with hyperlinks
        
    Returns:
        Tuple containing the generated summary, evaluation, and details about the models used
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    
    if not api_key:
        raise ValueError("OpenRouter API key not found. Please set the OPENROUTER_API_KEY environment variable.")
    
    # Format the arguments for the prompt
    arguments_text = "\n\n".join([f"Topic: {key}\nDescription: {value}" for key, value in arguments.items()])
    
    # Prepare content context if provided
    content_context = ""
    if content_text:
        max_content_length = 4000  # characters
        if len(content_text) > max_content_length:
            content_context = f"\n\nRELEVANT CONTENT (truncated):\n{content_text[:max_content_length]}..."
        else:
            content_context = f"\n\nRELEVANT CONTENT:\n{content_text}"
            
    # Add file contents if provided
    file_context = ""
    if file_contents:
        file_context = "\n\nREFERENCED FILES:\n"
        for filename, content in file_contents.items():
            max_file_length = 3000  # characters per file
            truncated_content = content[:max_file_length] + "..." if len(content) > max_file_length else content
            file_context += f"\n--- {filename} ---\n{truncated_content}\n"
    
    # First model provides a comprehensive analysis
    first_prompt = f"""
You are an expert at analyzing and summarizing complex information.

I need you to analyze the following topics/arguments:{content_context}{file_context}

ARGUMENTS TO ANALYZE:
{arguments_text}

Please provide a comprehensive analysis of these topics, focusing on:
1. Main ideas and key points
2. Supporting evidence or details
3. Contextual relevance
4. Relationships between different aspects of each topic
5. Information from referenced files when relevant

Your analysis will be passed to another AI for final summarization, so be thorough but organized.
"""

    try:
        # Call the first model for analysis
        logger.debug(f"Calling first model: {model1}")
        first_response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://agent.replit.com/",
                "X-Title": "Argument Summarizer"
            },
            json={
                "model": model1,
                "messages": [
                    {"role": "user", "content": first_prompt}
                ]
            }
        )
        
        first_response.raise_for_status()
        first_output = first_response.json()["choices"][0]["message"]["content"]
        
        # Second model synthesizes and refines
        second_prompt = f"""
You are an expert at synthesizing information and creating clear, concise summaries.

Another AI has analyzed the following topics and provided its analysis. Your job is to create a final, refined summary.

ORIGINAL TOPICS:
{arguments_text}

ANALYSIS FROM FIRST AI:
{first_output}

Please generate a polished final summary that:
1. Combines insights from the analysis
2. Highlights the most important points
3. Is well-structured and easy to understand
4. Provides a cohesive narrative connecting the topics (if applicable)
5. Is clear, concise, and informative

Format your response using appropriate markdown formatting to improve readability.
"""

        logger.debug(f"Calling second model: {model2}")
        second_response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://agent.replit.com/",
                "X-Title": "Argument Summarizer"
            },
            json={
                "model": model2,
                "messages": [
                    {"role": "user", "content": second_prompt}
                ]
            }
        )
        
        second_response.raise_for_status()
        final_summary = second_response.json()["choices"][0]["message"]["content"]
        
        # Third model evaluates the summary
        evaluation_prompt = f"""
You are an expert evaluator of summaries. Another AI has generated the following summary based on an analysis of topics and arguments.

SUMMARY TO EVALUATE:
{final_summary}

Please evaluate the summary based on:
1. Clarity and conciseness
2. Coverage of key points
3. Logical structure and flow
4. Accuracy and relevance to the original arguments and content

Provide a detailed evaluation and suggest any improvements if necessary.
"""

        logger.debug(f"Calling third model: {model3}")
        third_response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://agent.replit.com/",
                "X-Title": "Summary Evaluator"
            },
            json={
                "model": model3,
                "messages": [
                    {"role": "user", "content": evaluation_prompt}
                ]
            }
        )
        
        third_response.raise_for_status()
        evaluation = third_response.json()["choices"][0]["message"]["content"]
        
        # Prepare model details
        model_details = {
            "analysis_model": model1,
            "synthesis_model": model2,
            "evaluation_model": model3,
            "analysis_length": len(first_output),
            "summary_length": len(final_summary)
        }
        
        return final_summary, evaluation, model_details
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling OpenRouter API: {e}")
        if hasattr(e, 'response') and e.response:
            logger.error(f"Response: {e.response.text}")
        raise ValueError(f"Error calling OpenRouter API: {str(e)}")
    except Exception as e:
        logger.error(f"Error in generate_summary_with_openrouter: {e}")
        raise