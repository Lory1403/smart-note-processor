from datetime import datetime
import json
from database import db
from sqlalchemy import Table, Column, Integer, ForeignKey, Text, DateTime # Aggiunto Text, DateTime
from sqlalchemy.orm import relationship

class Document(db.Model):
    __tablename__ = 'documents' # Assicurati che il nome della tabella sia definito se non è lo standard
    
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    content = db.Column(db.Text, nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    file_type = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    topics = db.relationship('Topic', backref='document', cascade='all, delete-orphan')
    # Aggiungi questa relazione per collegare i messaggi di chat al documento
    chat_messages = db.relationship('ChatMessage', backref='document', lazy=True, cascade="all, delete-orphan")
    
    def __repr__(self):
        return f'<Document {self.title}>'


class Topic(db.Model):
    __tablename__ = 'topics' # Assicurati che il nome della tabella sia definito
    
    id = db.Column(db.Integer, primary_key=True)
    topic_id = db.Column(db.String(100), nullable=False)  # Unique ID for the topic from Gemini
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    document_id = db.Column(db.Integer, db.ForeignKey('documents.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    notes = db.relationship('Note', backref='topic', cascade='all, delete-orphan')
    image_analyses = relationship("ImageAnalysis", secondary='imageanalysis_topic', back_populates="topics")
    
    def __repr__(self):
        return f'<Topic {self.name}>'


class Note(db.Model):
    __tablename__ = 'notes'
    
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    format = db.Column(db.String(50), nullable=False)  # markdown, latex, html
    topic_id = db.Column(db.Integer, db.ForeignKey('topics.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<Note for {self.topic.name} in {self.format} format>'


# Tabella di associazione molti-a-molti
imageanalysis_topic = Table(
    'imageanalysis_topic',
    db.Model.metadata,
    Column('imageanalysis_id', Integer, ForeignKey('image_analyses.id')),
    Column('topic_id', Integer, ForeignKey('topics.id'))
)

class ImageAnalysis(db.Model):
    __tablename__ = 'image_analyses' # Assicurati che il nome della tabella sia definito
    
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    path = db.Column(db.String(255), nullable=False)
    analysis_result = db.Column(db.JSON, nullable=False)  # JSON string of analysis result
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    topics = relationship("Topic", secondary=imageanalysis_topic, back_populates="image_analyses")
    
    def __repr__(self):
        return f'<ImageAnalysis for {self.filename}>'
    
    def get_analysis_data(self):
        """Convert stored JSON string to Python dictionary"""
        try:
            return json.loads(self.analysis_result)
        except:
            return {}

# Nuovo modello per i messaggi della chat
class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'
    
    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey('documents.id'), nullable=False)
    sender = db.Column(db.String(50), nullable=False)  # "user" o "ai"
    message = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f'<ChatMessage {self.id} by {self.sender} at {self.timestamp}>'