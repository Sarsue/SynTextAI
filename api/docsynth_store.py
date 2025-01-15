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
from sklearn.metrics.pairwise import cosine_similarity
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)
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
    stripe_subscription_id = Column(String, nullable=False, unique=True, index=True)
    status = Column(String, nullable=False)
    current_period_end = Column(TIMESTAMP)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'))
    user = relationship("User", back_populates="subscriptions")


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
    
    # Relationship to chunks
    chunks = relationship("Chunk", back_populates="file", cascade="all, delete-orphan")
    
    user = relationship("User", back_populates="files")


class Chunk(Base):
    __tablename__ = 'chunks'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    content = Column(String)
    data = Column(JSON)  
    file_id = Column(Integer, ForeignKey('files.id', ondelete='CASCADE'))
    
    # Relationship to file
    file = relationship("File", back_populates="chunks")
    
    # Vector embedding for each chunk
    embedding = Column(Vector(1024), nullable=True)  # Example size (e.g., 1536 for OpenAI embeddings)
    def __repr__(self):
        return f"<Chunk(id={self.id}, file_id={self.file_id}, content={self.content[:50]}..., metadata={self.metadata})>"

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
        existing_user = session.query(User).filter_by(username=username).first()
        if existing_user:
            return {'id': existing_user.id, 'email': existing_user.email, 'username': existing_user.username}
        
        try:
            user = User(email=email, username=username)
            session.add(user)
            session.commit()
            return {'id': user.id, 'email': user.email, 'username': user.username}
        except IntegrityError:
            session.rollback()
            logger.error(f"Error adding user: {e}")
            raise

    def get_user_id_from_email(self, email):
        session = self.get_session()
        try:
            user = session.query(User).filter(User.email == email).first()
            return user.id
        finally:
            session.close()

    def add_or_update_subscription(self, user_id, stripe_customer_id, stripe_subscription_id, status, current_period_end=None):
        session = self.get_session()
        try:
            subscription = session.query(Subscription).filter(Subscription.stripe_subscription_id == stripe_subscription_id).first()
            if subscription:
                subscription.status = status
                subscription.current_period_end = current_period_end
            else:
                subscription = Subscription(
                    user_id=user_id, 
                    stripe_customer_id=stripe_customer_id, 
                    stripe_subscription_id=stripe_subscription_id,
                    status=status, 
                    current_period_end=current_period_end
                )
                session.add(subscription)
            session.commit()
            return {
                'id': subscription.id,
                'user_id': user_id,
                'stripe_customer_id': stripe_customer_id,
                'stripe_subscription_id': stripe_subscription_id,
                'status': status,
                'current_period_end': current_period_end
            }
        except Exception as e:
            session.rollback()
            logger.error(f"Error adding/updating subscription: {e}")
            raise
        finally:
            session.close()

    def update_subscription(self, stripe_customer_id, status, current_period_end):
        session = self.get_session()
        try:
            subscription = session.query(Subscription).filter_by(stripe_customer_id=stripe_customer_id).first()
            if subscription:
                subscription.status = status
                subscription.current_period_end = current_period_end
                subscription.updated_at = datetime.utcnow()  # Ensure `updated_at` is auto-updated
                session.commit()
            else:
                raise ValueError("Subscription not found.")
        except Exception as e:
            session.rollback()
            logger.error(f"Error updating subscription: {e}")
            raise
        finally:
            session.close()

    def get_subscription(self, user_id):
        session = self.get_session()
        try:
            subscription = session.query(Subscription).filter_by(user_id=user_id).first()
            if subscription:
                return {
                    'id': subscription.id,
                    'stripe_customer_id': subscription.stripe_customer_id,
                    'stripe_subscription_id': subscription.stripe_subscription_id,
                    'status': subscription.status,
                    'current_period_end': subscription.current_period_end,
                    'created_at': subscription.created_at,
                    'updated_at': subscription.updated_at
                }
            else:
                return None
        except Exception as e:
            logger.error(f"Error getting subscription: {e}")
            raise
        finally:
            session.close()

    def update_subscription_status(self, stripe_customer_id, new_status):
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
            logger.error(f"Error updating subscription status: {e}")
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
            logger.error(f"Error adding chat history: {e}")
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
            logger.error(f"Error adding message: {e}")
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
            logger.error(f"Error retrieving chat histories: {e}")
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
            logger.error(f"Error retrieving messages for chat history: {e}")
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

            logger.info(f'Deleted history {history_id} and associated messages for user {user_id}')
        except Exception as e:
            session.rollback()  # Rollback in case of error
            logger.error(f"Error deleting chat history: {e}")
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

            logger.info(f'Deleted all history and associated messages for user {user_id}')
        except Exception as e:
            session.rollback()  # Rollback in case of error
            logger.error(f"Error deleting all user histories: {e}")
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
            logger.error(f"Error formatting user chat history: {e}")
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
                logger.info(f"Adding file: {file_name} for user {user_id}")
                session.commit()
                logger.info(f"File added successfully: {new_file.id}")
                return {'id': new_file.id, 'user_id': user_id, 'file_url': file_url}
        except Exception as e:
            logger.error(f"Error adding file: {e}", exc_info=True)
            raise


    def update_file_with_chunks(self, user_id, file_name, file_type, chunks, embeddings):
        """
        Updates a file with its processed chunks and embeddings, handling different file types like PDF and video.
        
        Args:
            user_id (int): ID of the user who owns the file.
            file_name (str): Name of the file to update.
            file_type (str): Type of the file ('pdf' or 'video').
            chunks (list): List of content chunks extracted from the file.
            embeddings (list): List of embeddings corresponding to the chunks.
        """
        try:
            session = self.get_session()

            # Fetch the file by user_id and file_name
            file = session.query(File).filter(File.user_id == user_id, File.file_name == file_name).first()

            if file:
                # Ensure chunks and embeddings have the same length
                if len(chunks) != len(embeddings):
                    raise ValueError("Chunks and embeddings lists must have the same length.")

                # Create chunk objects and associate them with the file
                for i, chunk in enumerate(chunks):
                    # Set metadata based on the file type (PDF or video)
                    data = {}
                    # if file_type == 'pdf':
                    #     data = {"type": "pdf", "page_number": chunk["page_number"]}  # Assuming 'chunk' contains page_number
                    if file_type == 'video':
                        data = {"type": "video", "start_time": chunk["start_time"], "end_time": chunk["end_time"]}  # Assuming 'chunk' contains time intervals
                    else:
                        data = {"type": file_type, "page_number": chunk["page_number"]}
                    # Add the chunk to the database
                    new_chunk = Chunk(
                        file_id=file.id,
                        content=chunk["content"],
                        embedding=embeddings[i],
                        data=data
                    )
                    session.add(new_chunk)

                session.commit()
                logger.info(f"Updated file '{file_name}' for user {user_id} with {len(chunks)} chunks of type '{file_type}'.")
            else:
                raise ValueError(f"File '{file_name}' not found for user ID {user_id}.")

        except Exception as e:
            session.rollback()  # Rollback in case of error
            logger.error(f"Error updating file with chunks: {e}")
            raise  # Re-raise the exception for further handling
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
            logger.error(f"Error getting files for user: {e}")
            raise
        finally:
            session.close()

    def get_file_chunks(self, user_id, file_name):
        try:
            session = self.get_session()

            # Fetch the file by user_id and file_name
            file = session.query(File).filter(File.user_id == user_id, File.file_name == file_name).first()

            if file:
                # Fetch all chunks associated with the file
                chunks = session.query(Chunk).filter(Chunk.file_id == file.id).all()

                # Return the content of each chunk
                return [{'id': chunk.id, 'content': chunk.content, 'data': chunk.data} for chunk in chunks]
            else:
                raise ValueError("File not found")
        except Exception as e:
            logger.error(f"Error retrieving file chunks: {e}")
            raise
        finally:
            session.close()

    def delete_file_entry(self, user_id, file_id):
        """
        Deletes a file and its associated chunks from the database.
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

                # Delete associated chunks (assuming a relationship is defined on File for chunks)
                session.query(Chunk).filter(Chunk.file_id == file.id).delete()

                # Delete the file record
                session.delete(file)
                session.commit()

                logger.info(f"Deleted file '{file_name}' and its chunks for user {user_id}.")
                return {'file_name': file_name, 'file_id': file_id}
            else:
                raise ValueError(f"File with ID {file_id} not found for user ID {user_id}.")

        except Exception as e:
            session.rollback()  # Rollback in case of error
            logger.error(f"Error deleting file and its chunks: {e}")
            raise  # Re-raise the exception for further handling
        finally:
            session.close()

    def query_chunks_by_embedding(self, user_id, query_embedding, top_k=5):
        try:
            session = self.get_session()
            result = session.scalars(
                select(Chunk)
                .filter(Chunk.file_id.in_(select(File.id).filter(File.user_id == user_id)))
                .order_by(Chunk.embedding.l2_distance(query_embedding))
                .limit(top_k)
            ).all()

                # Prepare the result with all necessary metadata
                # List to hold (chunk, similarity) pairs
            similarities = []
            
            for chunk in result:
                similarity = cosine_similarity([query_embedding], [chunk.embedding])[0][0]
                similarities.append((chunk, similarity))
            
            # Sort chunks by similarity score in descending order
            similarities.sort(key=lambda x: x[1], reverse=True)

            # Collect top-k chunks and group them by page number
            top_chunks = []
            page_numbers = set()  # To avoid multiple chunks from the same page
            
            for chunk, _ in similarities[:top_k]:
                if chunk.data.get("page_number") not in page_numbers:
                    page_numbers.add(chunk.data.get("page_number"))
                    chunk_data = {
                        'chunk_id': chunk.id,
                        'chunk': chunk.content,
                        'data': chunk.data,  # Includes type-specific metadata
                        'file_url': chunk.file.file_url,  # Assuming file URL is a relation
                    }
                    # Add type-specific metadata
                    if chunk.data.get('type') == 'video':
                        chunk_data['start_time'] = chunk.data.get('start_time')
                        chunk_data['end_time'] = chunk.data.get('end_time')
                    else:  # Assuming it's a document
                        chunk_data['page_number'] = chunk.data.get('page_number')

                    top_chunks.append(chunk_data)

            return top_chunks
        except Exception as e:
            logger.error(f"Error querying chunks: {e}")
            raise
        finally:
            session.close()


   

# Example usage
# db = DocSynthStore("sqlite:///test.db")