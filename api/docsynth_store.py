from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey, TIMESTAMP, DateTime
from sqlalchemy.orm import declarative_base, relationship,  mapped_column, Mapped
from sqlalchemy.orm import sessionmaker
from sqlalchemy import func, select
from sqlalchemy.types import JSON
from pgvector.sqlalchemy import Vector
from sqlalchemy.exc import IntegrityError
from typing import List
from datetime import datetime
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
    summary = Column(Text, nullable=True) # Added summary column
    
    # Relationship to chunks
    chunks = relationship("Chunk", back_populates="file", cascade="all, delete-orphan")
    
    # Relationship to segments
    segments = relationship("Segment", back_populates="file", cascade="all, delete-orphan")
    
    user = relationship("User", back_populates="files")


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


class DocSynthStore:
    def __init__(self, database_url):
        self.database_url = database_url
        self.engine = create_engine(database_url, echo=True)
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
            ).outerjoin(Chunk, Chunk.file_id == File.id) \
            .filter(File.user_id == user_id) \
            .group_by(File.id, File.file_name, File.file_url) \
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
            #logger.error(f"Error getting files for user: {e}")
            raise
        finally:
            session.close()

    def get_files_by_user(self, user_id: int) -> list[dict]:
        """Retrieves all files for a given user, including their summary."""
        session = self.get_session()
        try:
            files = session.query(File).filter(File.user_id == user_id).order_by(File.created_at.desc()).all()
            # Return a list of dictionaries including the summary
            return [
                {
                    "id": file.id,
                    "file_name": file.file_name,
                    "file_url": file.file_url,
                    "created_at": file.created_at.isoformat(), # Format datetime
                    "user_id": file.user_id,
                    "summary": file.summary # Include the summary
                }
                for file in files
            ]
        except Exception as e:
            #logger.error(f"Error fetching files for user {user_id}: {e}")
            return [] # Return empty list on error
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

    def update_file_summary(self, file_id: int, summary: str):
        """Updates the summary field for a specific file."""
        session = self.get_session()
        try:
            db_file = session.query(File).filter(File.id == file_id).first()
            if db_file:
                db_file.summary = summary
                session.commit()
                #logger.info(f"Updated summary for file ID: {file_id}")
            else:
                #logger.error(f"File not found with ID {file_id} when trying to update summary.")
                raise ValueError(f"File not found with ID {file_id} when trying to update summary.")
        except Exception as e:
            session.rollback()
            #logger.error(f"Error updating summary for file ID {file_id}: {e}")
            raise
        finally:
            session.close()

    def get_user_by_email(self, email: str) -> User | None:
        session = self.get_session()
        try:
            user = session.query(User).filter(User.email == email).first()
            return user
        finally:
            session.close()

# Example usage
# db = DocSynthStore("sqlite:///test.db")