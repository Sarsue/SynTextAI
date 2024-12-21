from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey, TIMESTAMP, Index
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from datetime import datetime
import logging


logging.basicConfig(level=logging.INFO)
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

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'))
    file_name = Column(String)
    file_url = Column(String)
    extract = Column(Text)

    user = relationship("User", back_populates="files")


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
        try:
            user = User(email=email, username=username)
            session.add(user)
            session.commit()
            return {'id': user.id, 'email': user.email, 'username': user.username}
        except Exception as e:
            session.rollback()
            logging.error(f"Error adding user: {e}")
            raise
        finally:
            session.close()

    def get_user_id_from_email(self, email):
        session = self.get_session()
        try:
            user = session.query(User).filter(User.email == email).first()
            return user
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
            logging.error(f"Error adding/updating subscription: {e}")
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
            logging.error(f"Error adding chat history: {e}")
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
            logging.error(f"Error adding message: {e}")
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
            raise
        finally:
            session.close()

    def get_files_for_user(self, user_id):
        try:
            session = self.get_session()

            # Fetch the files for the user
            files = session.query(File.id, File.file_name, File.file_url).filter(File.user_id == user_id).all()

            file_info_list = []
            for file in files:
                file_info = {'id': file[0], 'name': file[1], 'publicUrl': file[2], 'processed': True}
                file_info_list.append(file_info)

            return file_info_list
        except Exception as e:
            logger.error(f"Error getting files for user: {e}")
            raise
        finally:
            session.close()

    def add_file(self, user_id, file_name, file_url):
        try:
            session = self.get_session()

            # Add a new file record
            new_file = File(user_id=user_id, file_name=file_name, file_url=file_url)
            session.add(new_file)
            session.commit()

            # Return the file information
            return {'id': new_file.id, 'user_id': user_id, 'file_url': file_url}
        except Exception as e:
            session.rollback()  # Rollback in case of error
            logger.error(f"Error adding file: {e}")
            raise
        finally:
            session.close()

    def update_file_with_extract(self, user_id, file_name, extract):
        try:
            session = self.get_session()

            # Fetch the file by user_id and file_name
            file = session.query(File).filter(File.user_id == user_id, File.file_name == file_name).first()

            if file:
                # Update the file's extract field
                file.extract = extract
                session.commit()
            else:
                raise ValueError("File not found")

        except Exception as e:
            session.rollback()  # Rollback in case of error
            logger.error(f"Error updating file with extract: {e}")
            raise
        finally:
            session.close()

    def get_file_extract(self, user_id, file_name):
        try:
            session = self.get_session()

            # Fetch the file by user_id and file_name
            file = session.query(File).filter(File.user_id == user_id, File.file_name == file_name).first()

            if file:
                return file.extract
            else:
                raise ValueError("File not found")
        except Exception as e:
            logger.error(f"Error retrieving file extract: {e}")
            raise
        finally:
            session.close()

    def delete_file_entry(self, user_id, file_id):
        try:
            session = self.get_session()

            # Fetch the file by user_id and file_id
            file = session.query(File).filter(File.user_id == user_id, File.id == file_id).first()

            if file:
                # Delete the file record
                file_name = file.file_name
                session.delete(file)
                session.commit()

                return {'file_name': file_name, 'file_id': file_id}
            else:
                raise ValueError("File not found")
        except Exception as e:
            session.rollback()  # Rollback in case of error
            logger.error(f"Error deleting file entry: {e}")
            raise
        finally:
            session.close()

# Example usage
# db = DocSynthStore("sqlite:///test.db")
