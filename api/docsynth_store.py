from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey, TIMESTAMP, DateTime, Float, Boolean
from sqlalchemy.orm import declarative_base, relationship,  mapped_column, Mapped
from sqlalchemy.orm import sessionmaker
from sqlalchemy import func, select
from sqlalchemy.types import JSON
from pgvector.sqlalchemy import Vector
from sqlalchemy.exc import IntegrityError
from typing import List, Optional
from datetime import datetime
import calendar
import logging
import numpy as np
from scipy.spatial.distance import cosine, euclidean



# logging.basicConfig(level=logging.ERROR)
# logger = logging.getLogger(__name__)
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=False, unique=True, index=True)
    username = Column(String, nullable=False, unique=True)

    subscriptions = relationship("Subscription", back_populates="user", cascade="all, delete-orphan")
    chat_histories = relationship("ChatHistory", back_populates="user", cascade="all, delete-orphan")
    files = relationship("File", back_populates="user", cascade="all, delete-orphan")
    messages = relationship("Message", back_populates="user", cascade="all, delete-orphan")


class Subscription(Base):
    __tablename__ = 'subscriptions'

    id = Column(Integer, primary_key=True)
    stripe_customer_id = Column(String, nullable=False)
    stripe_subscription_id = Column(String, nullable=True)
    status = Column(String, nullable=False)
    current_period_end = Column(TIMESTAMP)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)
    trial_end = Column(TIMESTAMP, nullable=True)


    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'))
    user = relationship("User", back_populates="subscriptions")

    # Link to CardDetails
    card_details = relationship("CardDetails", back_populates="subscription", cascade="all, delete-orphan")

class CardDetails(Base):
    __tablename__ = 'card_details'

    id = Column(Integer, primary_key=True)
    subscription_id = Column(Integer, ForeignKey('subscriptions.id', ondelete='CASCADE'), nullable=False)  # Link to the Subscription table
    card_last4 = Column(String(4), nullable=False)  # Last 4 digits of the card
    card_type = Column(String(50), nullable=False)  # Card type (e.g., Visa, Mastercard)
    exp_month = Column(Integer, nullable=False)  # Expiration month
    exp_year = Column(Integer, nullable=False)  # Expiration year
    created_at = Column(DateTime, default=datetime.utcnow)

    subscription = relationship('Subscription', back_populates='card_details')

class ChatHistory(Base):
    __tablename__ = 'chat_histories'

    id = Column(Integer, primary_key=True)
    title = Column(String, default="Untitled")
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'))
    user = relationship("User", back_populates="chat_histories")

    messages = relationship("Message", back_populates="chat_history", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = 'messages'

    id = Column(Integer, primary_key=True)
    content = Column(Text)
    sender = Column(String)
    timestamp = Column(TIMESTAMP, default=datetime.utcnow)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'))
    chat_history_id = Column(Integer, ForeignKey('chat_histories.id', ondelete='CASCADE'))

    user = relationship("User", back_populates="messages")
    chat_history = relationship("ChatHistory", back_populates="messages")



class File(Base):
    __tablename__ = 'files'
    
    id = Column(Integer, primary_key=True, autoincrement=True, unique=True)
    file_name = Column(String)
    file_url = Column(String)
    created_at = Column(DateTime, default=func.now())
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    processed = Column(Boolean, default=False)
    status = Column(String, nullable=True)
    error_message = Column(String, nullable=True)
    
    # Relationship to chunks
    chunks = relationship("Chunk", back_populates="file", cascade="all, delete-orphan")
    segments = relationship("Segment", back_populates="file", cascade="all, delete-orphan")
    user = relationship("User", back_populates="files")
    key_concepts = relationship("KeyConcept", backref="file", cascade="all, delete-orphan", lazy="selectin")
    flashcards = relationship("Flashcard", back_populates="file", cascade="all, delete-orphan")
    quiz_questions = relationship("QuizQuestion", back_populates="file", cascade="all, delete-orphan")


class Segment(Base):
    __tablename__ = 'segments'
    
    id = Column(Integer, primary_key=True, autoincrement=True, unique=True)
    page_number = Column(Integer)  # This represents the page number within the file
    content = Column(String)  # Content of the segment/page (optional, or could be derived from chunks)
    file_id = Column(Integer, ForeignKey('files.id', ondelete='CASCADE'))
    meta_data = Column(JSON, nullable=True) 
    
    # Relationship to file
    file = relationship("File", back_populates="segments")
    
    # Relationship to chunks
    chunks = relationship("Chunk", back_populates="segment", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Segment(id={self.id}, file_id={self.file_id}, page_number={self.page_number}, content={self.content[:50]}...)>"


class Chunk(Base):
    __tablename__ = 'chunks'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(Integer, ForeignKey('files.id', ondelete='CASCADE'))
    segment_id = Column(Integer, ForeignKey('segments.id', ondelete='CASCADE'))  # Link to the segment
    
    # Vector embedding for each chunk
    embedding = Column(Vector(1024), nullable=True)  # Example size (e.g., 1536 for OpenAI embeddings)
    
    # Relationship to file
    file = relationship("File", back_populates="chunks")
    
    # Relationship to segment
    segment = relationship("Segment", back_populates="chunks")
    
    def __repr__(self):
        return f"<Chunk(id={self.id}, file_id={self.file_id}, segment_id={self.segment_id}, embedding={self.embedding[:50]}...)>"


class KeyConcept(Base):
    __tablename__ = "key_concepts"

    id = Column(Integer, primary_key=True, index=True)
    file_id = Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True)
    
    concept_title = Column(String, nullable=False)
    concept_explanation = Column(Text, nullable=False)
    
    display_order = Column(Integer, nullable=True, default=0) 

    source_page_number = Column(Integer, nullable=True) 
    source_video_timestamp_start_seconds = Column(Integer, nullable=True)
    source_video_timestamp_end_seconds = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships for flashcards and quiz questions
    flashcards = relationship("Flashcard", back_populates="key_concept", cascade="all, delete-orphan")
    quiz_questions = relationship("QuizQuestion", back_populates="key_concept", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<KeyConcept(id={self.id}, file_id={self.file_id}, title='{self.concept_title[:30]}...')>"


class Flashcard(Base):
    __tablename__ = "flashcards"
    id = Column(Integer, primary_key=True)
    file_id = Column(Integer, ForeignKey('files.id', ondelete='CASCADE'), nullable=False)
    key_concept_id = Column(Integer, ForeignKey('key_concepts.id', ondelete='CASCADE'), nullable=False)
    question = Column(String, nullable=False)
    answer = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_custom = Column(Boolean, default=False)  # True if user-created/edited

    # Relationships
    file = relationship('File', back_populates='flashcards')
    key_concept = relationship('KeyConcept', back_populates='flashcards')

class QuizQuestion(Base):
    __tablename__ = "quiz_questions"
    id = Column(Integer, primary_key=True)
    file_id = Column(Integer, ForeignKey('files.id', ondelete='CASCADE'), nullable=False)
    key_concept_id = Column(Integer, ForeignKey('key_concepts.id', ondelete='CASCADE'), nullable=True)
    question = Column(String, nullable=False)
    question_type = Column(String, nullable=False)  # 'MCQ' or 'TF'
    correct_answer = Column(String, nullable=False)
    distractors = Column(JSON, nullable=True)  # List of wrong answers for MCQ
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    file = relationship('File', back_populates='quiz_questions')
    key_concept = relationship('KeyConcept', back_populates='quiz_questions')

class DocSynthStore:
    def __init__(self, database_url):
        self.database_url = database_url
        self.engine = create_engine(
            database_url,
            echo=True,
            pool_pre_ping=True,
            pool_recycle=1800  # Recycle connections every 30 minutes
        )
        self.Session = sessionmaker(bind=self.engine)
        self.create_tables()

    def create_tables(self):
        Base.metadata.create_all(self.engine)

    def get_session(self):
        return self.Session()

    def add_user(self, email, username):
        session = self.get_session()
        existing_user = session.query(User).filter_by(email=email).first()
        if existing_user:
            return {'id': existing_user.id, 'email': existing_user.email, 'username': existing_user.username}
        
        try:
            user = User(email=email, username=username)
            session.add(user)
            session.commit()
            return {'id': user.id, 'email': user.email, 'username': user.username}
        except IntegrityError:
            session.rollback()
            #logger.error(f"Error adding user: {e}")
            raise

    def get_user_id_from_email(self, email):
        session = self.get_session()
        try:
            user = session.query(User).filter(User.email == email).first()
            return user.id
        finally:
            session.close()
            
    def delete_user_account(self, user_id):
        session = self.get_session()
        try:
            # Fetch user and related records
            user = session.query(User).filter(User.id == user_id).first()
            if not user:
                raise ValueError(f"User with ID {user_id} not found.")

            # Fetch the user's subscription
            subscription = session.query(Subscription).filter(Subscription.user_id == user_id).first()
            if subscription:
                # Update subscription status to 'deleted' and clear stripe_subscription_id
                subscription.status = 'deleted'
                subscription.stripe_subscription_id = None
                session.add(subscription)

            # Delete related records, but don't delete user or subscription
            session.query(CardDetails).filter(CardDetails.subscription_id == subscription.id).delete(synchronize_session=False)
            session.query(Message).filter(Message.user_id == user_id).delete(synchronize_session=False)
            session.query(ChatHistory).filter(ChatHistory.user_id == user_id).delete(synchronize_session=False)
            session.query(File).filter(File.user_id == user_id).delete(synchronize_session=False)

            # Commit changes
            session.commit()

        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()

    def add_or_update_subscription(self, user_id, stripe_customer_id, stripe_subscription_id, status, current_period_end=None, trial_end=None, card_last4=None, card_type=None, exp_month=None, exp_year=None):
        session = self.get_session()
        update_time = datetime.utcnow()
        try:
            # Check if the subscription already exists
            subscription = session.query(Subscription).filter(Subscription.stripe_customer_id == stripe_customer_id).first()
            
            if subscription:
                # Update existing subscription
                subscription.stripe_subscription_id = stripe_subscription_id
                subscription.status = status
                subscription.current_period_end = current_period_end
                subscription.trial_end = trial_end  # Update trial_end if provided
                subscription.updated_at = update_time
                
                # Update card details if provided
                if card_last4 and card_type and exp_month and exp_year:
                    card_details = session.query(CardDetails).filter_by(subscription_id=subscription.id).first()
                    if card_details:
                        card_details.card_last4 = card_last4
                        card_details.card_type = card_type
                        card_details.exp_month = exp_month
                        card_details.exp_year = exp_year
                        card_details.created_at = update_time
                    else:
                        new_card_details = CardDetails(
                            subscription_id=subscription.id,
                            card_last4=card_last4,
                            card_type=card_type,
                            exp_month=exp_month,
                            exp_year=exp_year,
                            created_at=update_time
                        )
                        session.add(new_card_details)
            else:
                # Create a new subscription
                subscription = Subscription(
                    user_id=user_id,
                    stripe_customer_id=stripe_customer_id,
                    stripe_subscription_id=stripe_subscription_id,
                    status=status,
                    current_period_end=current_period_end,
                    trial_end=trial_end,  # Set trial_end if available
                    updated_at=update_time
                )
                session.add(subscription)
                session.flush()  # Ensure subscription.id is generated before using it
                
                # Add card details if provided
                if card_last4 and card_type and exp_month and exp_year:
                    new_card_details = CardDetails(
                        subscription_id=subscription.id,
                        card_last4=card_last4,
                        card_type=card_type,
                        exp_month=exp_month,
                        exp_year=exp_year,
                        created_at=update_time
                    )
                    session.add(new_card_details)

            session.commit()
            return {
                    'id': subscription.id,
                    'user_id': user_id,
                    'stripe_customer_id': stripe_customer_id,
                    'stripe_subscription_id': stripe_subscription_id,
                    'status': status,
                    'current_period_end': current_period_end,
                    'trial_end': trial_end.strftime("%Y-%m-%d %H:%M:%S") if trial_end else None,  # Include trial_end in the response
                    'card_last4': card_last4 if card_last4 else None,
                    'card_brand': card_type if card_type else None,
                    'exp_month': exp_month if exp_month else None,
                    'exp_year': exp_year if exp_year else None
                } 
        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()

    def update_subscription(self, stripe_customer_id, status, current_period_end, card_last4=None, card_type=None, exp_month=None, exp_year=None):
        session = self.get_session()
        try:
            # Find the subscription by Stripe customer ID
            subscription = session.query(Subscription).filter_by(stripe_customer_id=stripe_customer_id).first()
            if subscription:
                # Update subscription details
                subscription.status = status
                subscription.current_period_end = current_period_end
                subscription.updated_at = datetime.utcnow()  # Ensure `updated_at` is auto-updated

                # Update payment card details if provided
                if card_last4 and card_type and exp_month and exp_year:
                    card_details = session.query(CardDetails).filter_by(subscription_id=subscription.id).first()
                    if card_details:
                        # Update existing card details
                        card_details.card_last4 = card_last4
                        card_details.card_type = card_type
                        card_details.exp_month = exp_month
                        card_details.exp_year = exp_year
                        card_details.updated_at = datetime.utcnow()  # Ensure `updated_at` is updated
                    else:
                        # Add new card details if none exist
                        new_card_details = CardDetails(
                            subscription_id=subscription.id,
                            card_last4=card_last4,
                            card_type=card_type,
                            exp_month=exp_month,
                            exp_year=exp_year,
                            created_at=datetime.utcnow()
                        )
                        session.add(new_card_details)

                session.commit()
            else:
                raise ValueError("Subscription not found.")
        except Exception as e:
            session.rollback()
            #logger.error(f"Error updating subscription: {e}")
            raise
        finally:
            session.close()


    def get_subscription(self, user_id):
        session = self.get_session()
        try:
            # Use a LEFT JOIN to retrieve the subscription and its related card details (if any)
            subscription = (
                session.query(Subscription, CardDetails)
                .outerjoin(CardDetails, Subscription.id == CardDetails.subscription_id)  # Changed to outerjoin
                .filter(Subscription.user_id == user_id)
                .first()
            )
            
            if subscription:
                subscription_data, card_data = subscription
                # Handle the case where card_data might be None (i.e., no card details associated with this subscription)
               
                return {
                    'id': subscription_data.id,
                    'stripe_customer_id': subscription_data.stripe_customer_id,
                    'stripe_subscription_id': subscription_data.stripe_subscription_id,
                    'status': subscription_data.status,
                    'current_period_end': subscription_data.current_period_end,
                      'trial_end': subscription_data.trial_end,  
                    'created_at': subscription_data.created_at,
                    'updated_at': subscription_data.updated_at,
                    'card_last4': card_data.card_last4 if card_data else None,
                    'card_brand': card_data.card_type if card_data else None,
                    'exp_month': card_data.exp_month if card_data else None,
                    'exp_year': card_data.exp_year if card_data else None
                }
            else:
                return None
        except Exception as e:
            #logger.error(f"Error getting subscription: {e}")
            raise
        finally:
            session.close()


    def update_subscription_status(self, stripe_customer_id, new_status):
        """
            Called by Web Hook Uses Customer_id
        """
        session = self.get_session()
        try:
            subscription = session.query(Subscription).filter_by(stripe_customer_id=stripe_customer_id).first()
            if subscription:
                subscription.status = new_status
                subscription.updated_at = datetime.utcnow()  # Ensure `updated_at` is auto-updated
                session.commit()
            else:
                raise ValueError("Subscription not found.")
        except Exception as e:
            session.rollback()
            #logger.error(f"Error updating subscription status: {e}")
            raise
        finally:
            session.close()
    
    def add_chat_history(self, title, user_id):
        session = self.get_session()
        try:
            chat_history = ChatHistory(title=title, user_id=user_id)
            session.add(chat_history)
            session.commit()
            return {'id': chat_history.id, 'title': chat_history.title, 'user_id': chat_history.user_id}
        except Exception as e:
            session.rollback()
            #logger.error(f"Error adding chat history: {e}")
            raise
        finally:
            session.close()

    def get_latest_chat_history_id(self, user_id):
        session = self.get_session()
        try:
            chat_history = session.query(ChatHistory).filter(ChatHistory.user_id == user_id).order_by(ChatHistory.id.desc()).first()
            return chat_history.id if chat_history else None
        finally:
            session.close()

    def add_message(self, content, sender, user_id, chat_history_id=None):
        if not chat_history_id:
            chat_history_id = self.get_latest_chat_history_id(user_id)
            if not chat_history_id:
                file_history = self.add_chat_history("", user_id)
                chat_history_id = file_history["id"]

        session = self.get_session()
        try:
            message = Message(content=content, sender=sender, user_id=user_id, chat_history_id=chat_history_id)
            session.add(message)
            session.commit()
            return {
                'id': message.id,
                'content': message.content,
                'sender': message.sender,
                'timestamp': message.timestamp,
                'user_id': message.user_id,
                'chat_history_id': message.chat_history_id,
            }
        except Exception as e:
            session.rollback()
            #logger.error(f"Error adding message: {e}")
            raise
        finally:
            session.close()

    def get_all_user_chat_histories(self, user_id):
        session = self.get_session()
        try:
            chat_histories = (
                session.query(ChatHistory)
                .filter_by(user_id=user_id)
                .all()
            )

            result = []
            for count, chat_history in enumerate(chat_histories, start=1):
                print(count)
                messages = self.get_messages_for_chat_history(chat_history.id, user_id)

                result.append({
                    'id': chat_history.id,
                    'title': chat_history.title,
                    'messages': messages,
                })

            return result
        except Exception as e:
            #logger.error(f"Error retrieving chat histories: {e}")
            raise
        finally:
            session.close()

    def get_messages_for_chat_history(self, chat_history_id, user_id):
        session = self.get_session()
        try:
            messages = (
                session.query(Message)
                .filter_by(chat_history_id=chat_history_id, user_id=user_id)
                .all()
            )

            return [
                {
                    'id': message.id,
                    'content': message.content,
                    'sender': message.sender,
                    'timestamp': message.timestamp,
                    'user_id': message.user_id,
                }
                for message in messages
            ]
        except Exception as e:
            #logger.error(f"Error retrieving messages for chat history: {e}")
            raise
        finally:
            session.close()

    def delete_chat_history(self, user_id, history_id):
        try:
            session = self.get_session()

            # Delete messages associated with the chat history
            session.query(Message).filter(Message.chat_history_id == history_id, Message.user_id == user_id).delete()

            # Delete the chat history
            session.query(ChatHistory).filter(ChatHistory.id == history_id, ChatHistory.user_id == user_id).delete()

            # Commit the transaction
            session.commit()

            #logger.info(f'Deleted history {history_id} and associated messages for user {user_id}')
        except Exception as e:
            session.rollback()  # Rollback in case of error
            #logger.error(f"Error deleting chat history: {e}")
            raise
        finally:
            session.close()

    def delete_all_user_histories(self, user_id):
        try:
            session = self.get_session()

            # Delete all messages associated with the user
            session.query(Message).filter(Message.user_id == user_id).delete()

            # Delete all chat histories associated with the user
            session.query(ChatHistory).filter(ChatHistory.user_id == user_id).delete()

            # Commit the transaction
            session.commit()

            #logger.info(f'Deleted all history and associated messages for user {user_id}')
        except Exception as e:
            session.rollback()  # Rollback in case of error
            #logger.error(f"Error deleting all user histories: {e}")
            raise
        finally:
            session.close()

    def format_user_chat_history(self, chat_history_id, user_id):
        try:
            session = self.get_session()

            # Retrieve the last 5 messages for the given chat history and user
            messages = session.query(Message.content).filter(
                Message.chat_history_id == chat_history_id,
                Message.user_id == user_id
            ).order_by(Message.timestamp.desc()).limit(5).all()

            # Join the message contents into a formatted string
            chat_history = "\n".join([message[0] for message in messages])

            return chat_history.strip()  # Remove trailing newline
        except Exception as e:
            #logger.error(f"Error formatting user chat history: {e}")
            return ""
        finally:
            session.close()

    def add_file(self, user_id, file_name, file_url):
        try:
            with self.get_session() as session:
                user = session.query(User).filter(User.id == user_id).first()
                if not user:
                    raise ValueError(f"User with ID {user_id} does not exist.")
                
                new_file = File(user_id=user_id, file_name=file_name, file_url=file_url)
                session.add(new_file)
                #logger.info(f"Adding file: {file_name} for user {user_id}")
                session.commit()
                #logger.info(f"File added successfully: {new_file.id}")
                return {'id': new_file.id, 'user_id': user_id, 'file_url': file_url}
        except Exception as e:
            #logger.error(f"Error adding file: {e}", exc_info=True)
            raise


    def update_file_with_chunks(self,user_id, filename, file_type, extracted_data):
        """
        Stores the processed file data in the database with embeddings, segments, and metadata.

        Args:
            user_id (int): The ID of the user who owns the file.
            filename (str): The name of the file.
            file_type (str): The type of file (e.g., 'pdf', 'video').
            extracted_data (list): The processed data containing chunks and embeddings.
        """
        try:
            session = self.get_session()

            # Create or fetch the file entry
            file_entry = session.query(File).filter(File.user_id == user_id, File.file_name == filename).first()
            if not file_entry:
                file_entry = File(file_name=filename, file_type=file_type, user_id=user_id)
                session.add(file_entry)
                session.flush()  # Ensure file_id is generated

            # Process each segment/page
            for data in extracted_data:
                # Metadata for the segment
                meta_data = {}
                if file_type == 'video':
                    meta_data = {"type": "video", "start_time": data.get("start_time"), "end_time": data.get("end_time")}
                else:
                    meta_data = {"type": file_type, "page_number": data.get("page_num")}

                # Create segment entry with metadata
                segment_entry = Segment(
                    page_number=data.get("page_num"),  # For PDF documents
                    file_id=file_entry.id,
                    content = data.get("content"),
                    meta_data=meta_data  # Store metadata at the segment level
                )
                session.add(segment_entry)
                session.flush()  # Ensure segment_id is generated

                # Create chunk entries for each embedding
                page_chunks = data["chunks"]
                for chunk in page_chunks:
                    new_chunk = Chunk(
                        file_id=file_entry.id,
                        segment_id=segment_entry.id,
                        embedding=chunk["embedding"]
                    )
                    session.add(new_chunk)

            # Commit all changes
            session.commit()
            #logging.info(f"File '{filename}' stored successfully with processed data.")
        except Exception as e:
            session.rollback()
            #logging.error(f"Error storing file data: {e}")
            raise
        finally:
            session.close()

    def get_files_for_user(self, user_id):
        try:
            session = self.get_session()

            # Fetch the files for the user along with a count of their chunks
            files = session.query(
                File.id, 
                File.file_name, 
                File.file_url,
                func.count(Chunk.id).label('chunk_count')
            ).outerjoin(Chunk, Chunk.file_id == File.id)\
            .filter(File.user_id == user_id)\
            .group_by(File.id, File.file_name, File.file_url)\
            .all()

            file_info_list = []
            for file in files:
                file_info = {
                    'id': file[0],
                    'name': file[1],
                    'publicUrl': file[2],
                    'processed': file[3] > 0  # True if chunk_count > 0, otherwise False
                }
                file_info_list.append(file_info)

            return file_info_list

        except Exception as e:
            #logging.error(f"Error fetching files for user {user_id}: {e}")
            return []
        finally:
            session.close()

    def delete_file_entry(self, user_id, file_id):
        """
        Deletes a file, its associated chunks, and segments from the database.
        Args:
            user_id (int): ID of the user who owns the file.
            file_id (int): ID of the file to delete.
        """
        try:
            session = self.get_session()

            # Fetch the file by user_id and file_id
            file = session.query(File).filter(File.user_id == user_id, File.id == file_id).first()

            if file:
                # Log file name before deletion
                file_name = file.file_name

                # Delete associated chunks (including embeddings)
                session.query(Chunk).filter(Chunk.file_id == file.id).delete()

                # Delete associated segments
                session.query(Segment).filter(Segment.file_id == file.id).delete()

                # Delete the file record
                session.delete(file)
                session.commit()

                #logger.info(f"Deleted file '{file_name}', its chunks, and segments for user {user_id}.")
                return {'file_name': file_name, 'file_id': file_id}
            else:
                raise ValueError(f"File with ID {file_id} not found for user ID {user_id}.")

        except Exception as e:
            session.rollback()  # Rollback in case of error
            #logger.error(f"Error deleting file, its chunks, and segments: {e}")
            raise  # Re-raise the exception for further handling
        finally:
            session.close()

    def query_chunks_by_embedding(self, user_id, query_embedding, top_k=5,similarity_type='l2'):
        """
        Retrieves the top-k segments with the highest cosine similarity to the query embedding.

        Args:
            user_id (int): The ID of the user.
            query_embedding (list): The embedding of the user's query.
            top_k (int): The number of top similar segments to retrieve.

        Returns:
            list: A list of segments with the highest cosine or l2ß similarity to the query.
        """
        try:
            
            # Query chunks for the user and calculate cosine similarity using pgvector
            session = self.get_session()

            # Determine the similarity function to use
            if similarity_type == 'cosine':
                similarity_func = cosine
                order_by_clause = Chunk.embedding.op('<=>')(query_embedding)  # Use <=> for cosine similarity
            elif similarity_type == 'l2':
                similarity_func = euclidean
                order_by_clause = Chunk.embedding.op('<->')(query_embedding)  # Use <-> for L2 distance
            else:
                raise ValueError("similarity_type must be 'cosine' or 'l2'")

            # Query chunks for the user and calculate similarity using pgvector
            result = session.scalars(
                select(Chunk)
                .filter(Chunk.file_id.in_(select(File.id).filter(File.user_id == user_id)))
                .order_by(order_by_clause)
                .limit(top_k)
            ).all()

            # Prepare the top-k segments with additional information
            top_segments = []
            seen_segments = set()  # A set to track unique segment IDs
            for chunk in result:
                # Ensure that only distinct segments are added
                segment_entry = session.query(Segment).filter(Segment.id == chunk.segment_id).first()
                if segment_entry.id in seen_segments:
                    continue  # Skip if this segment has already been added
                seen_segments.add(segment_entry.id)

                file_entry = session.query(File).join(Segment).filter(Segment.id == chunk.segment_id).first()

                # Compute similarity score
                query_embedding_list = np.array(query_embedding)
                chunk_embedding_list = np.array(chunk.embedding)
                similarity_score = similarity_func(chunk_embedding_list, query_embedding_list)

                top_segments.append({
                    'meta_data': segment_entry.meta_data if segment_entry else None,
                    'similarity_score': similarity_score,
                    'file_name': file_entry.file_name if file_entry else None,
                    'file_url': file_entry.file_url if file_entry else None,
                    'page_number': segment_entry.page_number if segment_entry else None,
                    'content': segment_entry.content if segment_entry else None
                })

            return top_segments


        except Exception as e:
            #logger.error(f"Error retrieving similar segments: {e}")
            raise
        finally:
            session.close()

    def get_segments_for_page(self, file_id: int, page_number: int) -> List[str]:
        """Get all segment contents for a specific page of a file."""
        session = self.get_session()
        try:
            segments = session.query(Segment.content).filter(
                Segment.file_id == file_id,
                Segment.page_number == page_number
            ).all()
            # segments will be a list of tuples like [('content1',), ('content2',)]
            return [content for (content,) in segments]
        except Exception as e:
            logging.error(f"Error fetching segments for file {file_id}, page {page_number}: {e}")
            return [] # Return empty list on error
        finally:
            session.close()

    def get_segments_for_time_range(self, file_id: int, start_time: float, end_time: Optional[float] = None) -> List[str]:
        """Get segment contents for a specific time range of a video file.
        Assumes segments have 'start' and 'end' keys in meta_data.
        Retrieves segments that overlap with the requested [start_time, end_time].
        If end_time is None, retrieves all segments ending at or before start_time.
        """
        session = self.get_session()
        try:
            query = session.query(Segment.content).filter(
                Segment.file_id == file_id,
                Segment.meta_data.isnot(None) # Ensure meta_data exists
            )

            # --- Overlap Logic --- 
            # A segment [s_start, s_end] overlaps with request [r_start, r_end] if:
            # s_start < r_end AND s_end > r_start
            # We need to cast meta_data values to numeric types for comparison.
            # Using ->> to get text and ::float to cast in PostgreSQL.
            # Adjust casting if using a different DB.

            if end_time is not None:
                # Case 1: Request has a range [r_start, r_end]
                query = query.filter(
                    (Segment.meta_data['start'].astext.cast(Float) < end_time),
                    (Segment.meta_data['end'].astext.cast(Float) > start_time)
                )
            else:
                # Case 2: Request has only a start time [r_start]
                # Fetch all segments ending at or before r_start
                query = query.filter(
                    (Segment.meta_data['end'].astext.cast(Float) <= start_time)
                )
            # --- End Overlap Logic ---

            # Order by start time for coherence
            query = query.order_by(Segment.meta_data['start'].astext.cast(Float))

            segments = query.all()
            return [content for (content,) in segments]
        except Exception as e:
            # Log the error, including potential issues with meta_data structure or casting
            logging.error(f"Error fetching segments for file {file_id}, time {start_time}-{end_time}: {e}", exc_info=True)
            return [] # Return empty list on error
        finally:
            session.close()

    def add_key_concept(self, 
                        file_id: int, 
                        concept_title: str, 
                        concept_explanation: str, 
                        display_order: Optional[int] = None, 
                        source_page_number: Optional[int] = None, 
                        source_video_timestamp_start_seconds: Optional[int] = None, 
                        source_video_timestamp_end_seconds: Optional[int] = None) -> int:
        """Adds a new key concept associated with a file."""
        session = self.get_session()
        try:
            new_concept = KeyConcept(
                file_id=file_id,
                concept_title=concept_title,
                concept_explanation=concept_explanation,
                display_order=display_order,
                source_page_number=source_page_number,
                source_video_timestamp_start_seconds=source_video_timestamp_start_seconds,
                source_video_timestamp_end_seconds=source_video_timestamp_end_seconds
            )
            session.add(new_concept)
            session.commit()
            session.refresh(new_concept) # To get the ID and other server-generated defaults
            return new_concept.id
        except Exception as e:
            session.rollback()
            logging.error(f"Error adding key concept for file_id {file_id}: {e}", exc_info=True)
            raise
        finally:
            session.close()

    def add_flashcard(self, file_id: int, key_concept_id: int, question: str, answer: str, is_custom: bool = False) -> int:
        """Adds a new flashcard linked to a file and key concept."""
        session = self.get_session()
        try:
            new_flashcard = Flashcard(
                file_id=file_id,
                key_concept_id=key_concept_id,
                question=question,
                answer=answer,
                is_custom=is_custom
            )
            session.add(new_flashcard)
            session.commit()
            session.refresh(new_flashcard)
            return new_flashcard.id
        except Exception as e:
            session.rollback()
            logging.error(f"Error adding flashcard for file_id {file_id}, key_concept_id {key_concept_id}: {e}", exc_info=True)
            raise
        finally:
            session.close()

    def add_quiz_question(self, file_id: int, key_concept_id: Optional[int], question: str, question_type: str, correct_answer: str, distractors: Optional[list] = None) -> int:
        """Adds a new quiz question (MCQ or TF) linked to a file and optionally a key concept."""
        session = self.get_session()
        try:
            new_quiz = QuizQuestion(
                file_id=file_id,
                key_concept_id=key_concept_id,
                question=question,
                question_type=question_type,
                correct_answer=correct_answer,
                distractors=distractors
            )
            session.add(new_quiz)
            session.commit()
            session.refresh(new_quiz)
            return new_quiz.id
        except Exception as e:
            session.rollback()
            logging.error(f"Error adding quiz question for file_id {file_id}, key_concept_id {key_concept_id}: {e}", exc_info=True)
            raise
        finally:
            session.close()

    def get_file_by_id(self, file_id: int) -> Optional[File]:
        """Get a file by its ID"""
        session = self.get_session()
        try:
            file = session.query(File).filter(File.id == file_id).first()
            return file
        finally:
            session.close()
            
    def get_file_by_name(self, user_id: int, filename: str) -> Optional[File]:
        """Get a file record by user ID and filename."""
        session = self.get_session()
        try:
            file_record = session.query(File).filter(
                File.user_id == user_id,
                File.file_name == filename
            ).order_by(File.created_at.desc()).first() # Get the most recent if multiple exist
            return file_record
        finally:
            session.close()

    def is_premium_user(self, user_id: int) -> bool:
        """Check if a user has an active premium subscription
        
        Args:
            user_id: The ID of the user to check
            
        Returns:
            bool: True if the user has an active premium subscription, False otherwise
        """
        session = self.get_session()
        try:
            subscription = session.query(Subscription).filter(
                Subscription.user_id == user_id,
                Subscription.status.in_(["active", "trialing"])
            ).first()
            
            # User is premium if they have an active or trialing subscription
            return subscription is not None
        finally:
            session.close()
            
    def get_flashcards_for_file(self, file_id: int):
        """Get all flashcards for a given file."""
        session = self.get_session()
        try:
            return session.query(Flashcard).filter(Flashcard.file_id == file_id).all()
        finally:
            session.close()

    def get_quiz_questions_for_file(self, file_id: int):
        """Get all quiz questions for a given file."""
        session = self.get_session()
        try:
            return session.query(QuizQuestion).filter(QuizQuestion.file_id == file_id).all()
        finally:
            session.close()

    def update_file_processing_status(self, file_id: int, processed: bool, status: str = None, error_message: str = None) -> bool:
        """Update the processing status of a file
        
        Args:
            file_id: ID of the file to update
            processed: New processing status (True if processed, False if needs processing)
            status: Optional status string (e.g., 'success', 'failed', 'warning')
            error_message: Optional error message when processing failed
            
        Returns:
            bool: True if successful, False otherwise
        """
        session = self.get_session()
        try:
            file = session.query(File).filter(File.id == file_id).first()
            if not file:
                logging.error(f"File with ID {file_id} not found")
                return False
                
            file.processed = processed
            
            # Store additional status information if provided
            if status:
                # Add status attribute if it doesn't exist yet in the database
                # This is safe since SQLAlchemy will ignore setting attributes that don't exist in the model
                file.status = status
                
            # Store error message if provided
            if error_message:
                # Add error_message attribute if it doesn't exist yet
                file.error_message = error_message
                
            session.commit()
            log_msg = f"Updated processing status for file ID {file_id} to {processed}"
            if status:
                log_msg += f", status: {status}"
            if error_message:
                log_msg += f", error: {error_message[:50]}{'...' if len(error_message) > 50 else ''}"
            logging.info(log_msg)
            return True
        except Exception as e:
            session.rollback()
            logging.error(f"Error updating processing status for file ID {file_id}: {e}")
            return False
        finally:
            session.close()
    
# Example usage
# db = DocSynthStore("sqlite:///test.db")