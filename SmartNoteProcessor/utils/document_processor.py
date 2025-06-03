import os
import logging
from typing import Dict, List, Any, Optional, Tuple
import re
import tempfile

# Import file type specific libraries
try:
    import PyPDF2
except ImportError:
    PyPDF2 = None

try:
    from pdfminer.high_level import extract_text as pdfminer_extract_text
except ImportError:
    pdfminer_extract_text = None

try:
    import docx
except ImportError:
    docx = None

try:
    import whisper
    # Consider loading the model once if performance is critical
    # whisper_model = whisper.load_model("base") # Or "small", "medium", "large"
except ImportError:
    whisper = None

try:
    from moviepy.editor import VideoFileClip
except ImportError:
    VideoFileClip = None

from utils.topic_extractor import TopicExtractor
from utils.summary_extractor import SummaryExtractor

logger = logging.getLogger(__name__)

class DocumentProcessor:
    """
    Handles document processing, including text extraction from various file formats
    and topic information extraction from the document content.
    """
    
    def __init__(self, gemini_api_key=None):
        """
        Initialize the document processor.
        
        Args:
            gemini_api_key: API key for GeminiClient (optional)
        """
        # self.openrouter_client = OpenrouterClient(api_key=gemini_api_key) if gemini_api_key else None # Assuming this is how it's initialized
        # Define supported extensions
        self.pdf_extensions = ['.pdf']
        self.docx_extensions = ['.docx']
        self.txt_extensions = ['.txt']
        self.video_extensions = ['.mp4', '.mov', '.avi', '.mkv']
        self.audio_extensions = ['.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac'] # Added audio extensions
        self.image_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff']

    def extract_text(self, file_path: str, original_filename: str) -> str:
        """
        Extract text from a document file based on its format.
        
        Args:
            file_path: Path to the temporary file or persistent file
            original_filename: Original name of the uploaded file
            
        Returns:
            Extracted text from the document
        
        Raises:
            ValueError: If the file format is not supported or extraction fails
        """
        _, ext = os.path.splitext(original_filename)
        ext = ext.lower()

        if ext in self.pdf_extensions:
            return self._extract_text_from_pdf(file_path)
        elif ext in self.docx_extensions:
            return self._extract_text_from_docx(file_path)
        elif ext in self.txt_extensions:
            return self._extract_text_from_txt(file_path)
        elif ext in self.video_extensions:
            return self._extract_text_from_video(file_path)
        elif ext in self.audio_extensions: # Handling for audio files
            return self._extract_text_from_audio(file_path)
        elif ext in self.image_extensions:
            logger.info(f"Image file '{original_filename}' received in extract_text. Image content is processed separately.")
            return f"Placeholder for image file: {original_filename}" 
        else:
            logger.warning(f"Unsupported file type for direct text extraction: {original_filename}")
            return f"Unsupported file type for direct text extraction: {original_filename}"
    
    def _extract_text_from_txt(self, file_path: str) -> str:
        """
        Extract text from a plain text file.
        
        Args:
            file_path: Path to the text file
            
        Returns:
            Text content of the file
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                return file.read()
        except UnicodeDecodeError:
            # Try different encodings if UTF-8 fails
            try:
                with open(file_path, 'r', encoding='latin-1') as file:
                    return file.read()
            except Exception as e:
                logger.error(f"Error reading text file with latin-1 encoding: {str(e)}")
                raise ValueError(f"Failed to read text file: {str(e)}")
        except Exception as e:
            logger.error(f"Error reading text file: {str(e)}")
            raise ValueError(f"Failed to read text file: {str(e)}")
    
    def _extract_text_from_pdf(self, file_path: str) -> str:
        """
        Extract text from a PDF file.
        
        Args:
            file_path: Path to the PDF file
            
        Returns:
            Extracted text from the PDF
            
        Raises:
            ValueError: If PDF extraction libraries are not available or extraction fails
        """
        text = ""
        
        # Try PyPDF2 first
        if PyPDF2:
            try:
                with open(file_path, 'rb') as file:
                    reader = PyPDF2.PdfReader(file)
                    for page_num in range(len(reader.pages)):
                        page = reader.pages[page_num]
                        text += page.extract_text() + "\n\n"
                
                # If we got reasonable text, return it
                if len(text.strip()) > 100:
                    return text
            except Exception as e:
                logger.warning(f"PyPDF2 extraction failed: {str(e)}")
        
        # Fall back to pdfminer if PyPDF2 fails or gets too little text
        if pdfminer_extract_text:
            try:
                text = pdfminer_extract_text(file_path)
                return text
            except Exception as e:
                logger.error(f"pdfminer extraction failed: {str(e)}")
        
        # If both failed
        if not text.strip():
            raise ValueError("Failed to extract text from PDF. Ensure the PDF contains extractable text.")
        
        return text
    
    def _extract_text_from_docx(self, file_path: str) -> str:
        """
        Extract text from a DOCX file.
        
        Args:
            file_path: Path to the DOCX file
            
        Returns:
            Extracted text from the DOCX
            
        Raises:
            ValueError: If python-docx library is not available or extraction fails
        """
        if not docx:
            raise ValueError("python-docx library is not available. Cannot extract text from DOCX files.")
        
        try:
            doc = docx.Document(file_path)
            text = ""
            for para in doc.paragraphs:
                text += para.text + "\n"
            return text
        except Exception as e:
            logger.error(f"Error extracting text from DOCX: {str(e)}")
            raise ValueError(f"Failed to extract text from DOCX: {str(e)}")
    
    def _extract_text_from_video(self, file_path: str) -> str:
        """
        Extract text (transcription) from a video file using Whisper.
        
        Args:
            file_path: Path to the video file
            
        Returns:
            Transcribed text from the video's audio
            
        Raises:
            ValueError: If required libraries are missing or transcription fails
        """
        if not VideoFileClip:
            raise ValueError("moviepy library is not available. Cannot process video files.")
        if not whisper:
            raise ValueError("openai-whisper library is not available. Cannot transcribe video files.")
        
        audio_path = None
        temp_dir = tempfile.mkdtemp()
        try:
            logger.info(f"Extracting audio from video: {file_path}")
            # Extract audio using moviepy
            video = VideoFileClip(file_path)
            # Use a temporary file for the audio
            audio_filename = os.path.splitext(os.path.basename(file_path))[0] + ".mp3"
            audio_path = os.path.join(temp_dir, audio_filename)
            video.audio.write_audiofile(audio_path, codec='mp3')
            video.close() # Close the video file handle
            logger.info(f"Audio extracted to temporary file: {audio_path}")

            # Transcribe audio using Whisper
            logger.info(f"Transcribing audio file: {audio_path}")
            # Load model here or use pre-loaded model
            model = whisper.load_model("base") # Choose model size based on needs/resources
            result = model.transcribe(audio_path, fp16=False) # fp16=False might be needed on some CPUs
            transcription = result["text"]
            logger.info(f"Transcription complete. Length: {len(transcription)} chars")
            
            return transcription

        except Exception as e:
            logger.error(f"Error processing video file {file_path}: {str(e)}")
            raise ValueError(f"Failed to extract text from video: {str(e)}")
        finally:
            # Clean up temporary audio file and directory
            if audio_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                except Exception as rm_err:
                     logger.error(f"Error removing temporary audio file {audio_path}: {rm_err}")
            try:
                shutil.rmtree(temp_dir)
            except Exception as rmdir_err:
                 logger.error(f"Error removing temporary directory {temp_dir}: {rmdir_err}")

    def _extract_text_from_audio(self, file_path: str) -> str:
        """
        Extract text (transcription) from an audio file using Whisper.
        
        Args:
            file_path: Path to the audio file
            
        Returns:
            Transcribed text from the audio
            
        Raises:
            ValueError: If required libraries are missing or transcription fails
        """
        if not whisper:
            raise ValueError("openai-whisper library is not available. Cannot transcribe audio files.")
        
        try:
            logger.info(f"Transcribing audio file: {file_path}")
            # Consider making model choice configurable or using a smaller default if performance is an issue
            model = whisper.load_model("base") 
            result = model.transcribe(file_path, fp16=False) # fp16=False might be needed on some CPUs
            transcription = result["text"]
            logger.info(f"Audio transcription complete. Length: {len(transcription)} chars")
            return transcription
        except Exception as e:
            logger.error(f"Error processing audio file {file_path}: {str(e)}", exc_info=True)
            raise ValueError(f"Failed to extract text from audio: {str(e)}")
    
    def extract_topics(self, document_content: str, granularity: int) -> Dict[str, Dict[str, Any]]:
        try:
            
            topics = TopicExtractor.extract_topics(document_content, granularity)
            
            # Log the number of topics found
            logger.info(f"Created {len(topics)} topics with {granularity}% granularity")
            
            return topics
        except Exception as e:
            logger.error(f"Error in topic extraction: {str(e)}")
            # Return a single error topic in case of failure
            return {
                'error': {
                    'name': 'Error in topic extraction',
                    'description': f"Failed to extract topics: {str(e)}"
                }
            }
    
    def extract_resumes(self, document_content: str, topic_name: str) -> str:
        try:
            resume = SummaryExtractor.extract_resumes(self, document_content, topic_name)
            
            # Log the number of topics found
            logger.info(f"Created resume for {topic_name}")
            
            return resume
        except Exception as e:
            logger.error(f"Error extracting topic information: {str(e)}")
            return f"Error extracting information for topic '{topic_name}': {str(e)}"
