import psycopg2
from psycopg2 import sql, OperationalError, errors
from datetime import datetime
import time
from llm_service import get_text_embedding
import pickle
from scipy.spatial.distance import cosine
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# DocSynthStore class definition
class DocSynthStore:
    def __init__(self, database_config):
        self.database_config = database_config
        self.vectorizer = TfidfVectorizer()
        self.create_tables()

    # Connection management methods
    def get_connection(self, retries=3, delay=2):
        for attempt in range(retries):
            try:
                return psycopg2.connect(**self.database_config)
            except psycopg2.OperationalError as e:
                logger.error(f"Error getting connection (Attempt {attempt + 1}): {e}")
                if attempt < retries - 1:
                    time.sleep(delay)  # Wait before retrying
                else:
                    raise e  # Re-raise the exception after all retries

    def release_connection(self, connection):
        connection.close()

    def create_tables(self):
        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute('''
                        CREATE TABLE IF NOT EXISTS users (
                            id SERIAL PRIMARY KEY,
                            email TEXT NOT NULL UNIQUE,
                            username TEXT NOT NULL UNIQUE
                        )
                    ''')

                    cursor.execute('''
                        CREATE TABLE IF NOT EXISTS subscriptions (
                            id SERIAL PRIMARY KEY,
                            stripe_customer_id TEXT NOT NULL,
                            stripe_subscription_id TEXT NOT NULL UNIQUE,
                            status TEXT NOT NULL,
                            user_id INTEGER,
                            current_period_end TIMESTAMP,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (user_id) REFERENCES users (id),
                            UNIQUE (stripe_customer_id, stripe_subscription_id)
                        )
                    ''')

                    cursor.execute('''
                        CREATE TABLE IF NOT EXISTS chat_histories (
                            id SERIAL PRIMARY KEY,
                            title TEXT,
                            user_id INTEGER,
                            FOREIGN KEY (user_id) REFERENCES users (id)
                        )
                    ''')

                    cursor.execute('''
                        CREATE TABLE IF NOT EXISTS messages (
                            id SERIAL PRIMARY KEY,
                            content TEXT,
                            sender TEXT,
                            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            user_id INTEGER,
                            chat_history_id INTEGER,
                            FOREIGN KEY (user_id) REFERENCES users (id),
                            FOREIGN KEY (chat_history_id) REFERENCES chat_histories (id)
                        )
                    ''')

                    cursor.execute('''
                        CREATE TABLE IF NOT EXISTS files (
                            id SERIAL PRIMARY KEY,
                            user_id INTEGER,
                            file_name TEXT,
                            file_url TEXT,
                            extract TEXT,  
                            FOREIGN KEY (user_id) REFERENCES users (id)
                        )
                    ''')
        except psycopg2.OperationalError as e:
            logger.error(f"Database operation error: {e}")
            raise  # Optional: re-raise to handle at a higher level
        except Exception as e:
            logger.error(f"Error creating tables: {e}")
        finally:
            self.release_connection(connection)  # Ensure connection is released

    # User management methods
    def add_user(self, email, username):
        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    try:
                        # Check if the user already exists
                        cursor.execute('''
                            SELECT id, email, username
                            FROM users
                            WHERE email = %s
                        ''', (email,))
                        user = cursor.fetchone()

                        if user:
                            return {'id': user[0], 'email': user[1], 'username': user[2]}

                        # Insert the new user
                        cursor.execute('''
                            INSERT INTO users (email, username)
                            VALUES (%s, %s)
                            RETURNING id, email, username
                        ''', (email, username))

                        user = cursor.fetchone()
                        return {'id': user[0], 'email': user[1], 'username': user[2]}

                    except errors.UniqueViolation as e:
                        logging.error(f"Error adding user: {e}")
                        connection.rollback()
                        cursor.execute('''
                            SELECT id, email, username
                            FROM users
                            WHERE email = %s
                        ''', (email,))
                        user = cursor.fetchone()
                        return {'id': user[0], 'email': user[1], 'username': user[2]}
        finally:
            self.release_connection(connection)

    def get_user_id_from_email(self, email):
        connection = self.get_connection()
        try:
            logging.info(f"Attempting to retrieve user ID for email: {email}")
            
            with connection.cursor() as cursor:
                cursor.execute('''
                    SELECT id FROM users WHERE email = %s
                ''', (email,))
                result = cursor.fetchone()

            if result:
                logging.info(f"User ID found for email {email}: {result[0]}")
            else:
                logging.warning(f"No user ID found for email: {email}")

            return result[0] if result else None
        
        except Exception as e:
            logger.exception(f"Error getting user ID from email: {e}")
            raise  # Ensure the exception is propagated for debugging purposes
        
        finally:
            logging.info("Releasing database connection")
            self.release_connection(connection)


    # Subscription  management methods
    def add_or_update_subscription(self, user_id, stripe_customer_id, stripe_subscription_id, status, current_period_end=None):
        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute('''
                        INSERT INTO subscriptions (user_id, stripe_customer_id, stripe_subscription_id, status, current_period_end, updated_at)
                        VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                        ON CONFLICT (stripe_subscription_id)
                        DO UPDATE SET status = EXCLUDED.status, current_period_end = EXCLUDED.current_period_end, updated_at = CURRENT_TIMESTAMP
                        RETURNING id;
                    ''', (user_id, stripe_customer_id, stripe_subscription_id, status, current_period_end))

                    subscription_id = cursor.fetchone()[0]

                connection.commit()
                return {
                    'id': subscription_id,
                    'user_id': user_id,
                    'stripe_customer_id': stripe_customer_id,
                    'stripe_subscription_id': stripe_subscription_id,
                    'status': status,
                    'current_period_end': current_period_end
                }
        except Exception as e:
            logger.error(f"Error adding or updating subscription: {e}")
            raise
        finally:
            self.release_connection(connection)

    def update_subscription(self, stripe_customer_id, status, current_period_end):
        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute('''
                        UPDATE subscriptions
                        SET status = %s, current_period_end = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE stripe_customer_id = %s;
                    ''', (status, current_period_end, stripe_customer_id))

                connection.commit()
        except Exception as e:
            logger.error(f"Error updating subscription: {e}")
            raise
        finally:
            self.release_connection(connection)

    def get_subscription(self, user_id):
        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute('''
                        SELECT id, stripe_customer_id, stripe_subscription_id, status, current_period_end, created_at, updated_at
                        FROM subscriptions
                        WHERE user_id = %s;
                    ''', (user_id,))

                    result = cursor.fetchone()

                    if result:
                        subscription = {
                            'id': result[0],
                            'stripe_customer_id': result[1],
                            'stripe_subscription_id': result[2],
                            'status': result[3],
                            'current_period_end': result[4],
                            'created_at': result[5],
                            'updated_at': result[6]
                        }
                        return subscription
                    else:
                        return None
        except Exception as e:
            logger.error(f"Error getting subscription: {e}")
            raise
        finally:
            self.release_connection(connection)

    def update_subscription_status(self, stripe_customer_id, new_status):
        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute('''
                        UPDATE subscriptions
                        SET status = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE stripe_customer_id = %s;
                    ''', (new_status, stripe_customer_id))

                connection.commit()
        except Exception as e:
            logger.error(f"Error updating subscription status: {e}")
            raise
        finally:
            self.release_connection(connection)

    # Chat Message & History  management methods
    def add_chat_history(self, title, user_id):
        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute('''
                        INSERT INTO chat_histories (title, user_id)
                        VALUES (%s, %s)
                        RETURNING id
                    ''', (title, user_id))

                    chat_history_id = cursor.fetchone()[0]
                    return {'id': chat_history_id, 'title': title, 'user_id': user_id}
        except Exception as e:
            logger.error(f"Error adding chat history: {e}")
            raise
        finally:
            self.release_connection(connection)

    def get_latest_chat_history_id(self, user_id):
        """Fetch the latest chat history ID for a given user."""

        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute('''
                        SELECT id
                        FROM chat_histories
                        WHERE user_id = %s
                        ORDER BY id DESC  -- Assuming you have a timestamp column
                        LIMIT 1
                    ''', (user_id,))

                    result = cursor.fetchone()
                    return result[0] if result else None
        except Exception as e:
            logger.error(f"Error fetching latest chat history ID: {e}")
            raise
        finally:
            self.release_connection(connection)

    def add_message(self, content, sender, user_id, chat_history_id=None):
        if chat_history_id is None:
            chat_history_id = self.get_latest_chat_history_id(user_id)

        if chat_history_id is None:
            file_history = self.add_chat_history("", user_id)
            chat_history_id = file_history["id"]

        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute('''
                        INSERT INTO messages (content, sender, user_id, chat_history_id)
                        VALUES (%s, %s, %s, %s)
                        RETURNING id, timestamp
                    ''', (content, sender, user_id, chat_history_id))

                    message_id, timestamp = cursor.fetchone()

                    return {
                        'id': message_id,
                        'content': content,
                        'sender': sender,
                        'timestamp': timestamp,
                        'user_id': user_id,
                        'chat_history_id': chat_history_id,
                    }
        except Exception as e:
            logger.error(f"Error adding message: {e}")
            raise
        finally:
            self.release_connection(connection)

    def get_all_user_chat_histories(self, user_id):
        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute('''
                        SELECT id, title
                        FROM chat_histories
                        WHERE user_id = %s
                    ''', (user_id,))

                    rows = cursor.fetchall()

                    chat_histories = []
                    count = 1
                    for row in rows:
                        print(count)
                        chat_history_id = row[0]
                        chat_history_title = row[1]

                        # For each chat history, fetch the corresponding messages
                        messages = self.get_messages_for_chat_history(
                            chat_history_id, user_id)

                        chat_history = {
                            'id': chat_history_id,
                            'title': chat_history_title,
                            'messages': messages,
                        }

                        chat_histories.append(chat_history)
                        count = count + 1

                    return chat_histories
        except Exception as e:
            logger.error(f"Error retrieving chat histories: {e}")
            raise
        finally:
            self.release_connection(connection)

    def get_messages_for_chat_history(self, chat_history_id, user_id):
        connection = self.get_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute('''
                    SELECT id, content, sender, timestamp, user_id
                    FROM messages
                    WHERE chat_history_id = %s AND user_id = %s
                ''', (chat_history_id, user_id))

                rows = cursor.fetchall()

                messages = []
                for row in rows:
                    message = {
                        'id': row[0],
                        'content': row[1],
                        'sender': row[2],
                        'timestamp': row[3],
                        'user_id': row[4],
                    }

                    messages.append(message)

                return messages
        except Exception as e:
            logger.error(f"Error retrieving messages for chat history: {e}")
            raise
        finally:
            self.release_connection(connection)

    def delete_chat_history(self, user_id, history_id):
        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    # Delete messages associated with the chat history
                    cursor.execute('''
                        DELETE FROM messages
                        WHERE chat_history_id = %s AND user_id = %s
                    ''', (history_id, user_id))

                    # Delete the chat history
                    cursor.execute('''
                        DELETE FROM chat_histories
                        WHERE id = %s AND user_id = %s
                    ''', (history_id, user_id))
            print(
                f'Deleted history {history_id} and associated messages for user {user_id}')
        except Exception as e:
            logger.error(f"Error deleting chat history: {e}")
            raise
        finally:
            self.release_connection(connection)

    def delete_all_user_histories(self, user_id):
        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    # Delete all messages associated with the user
                    cursor.execute('''
                        DELETE FROM messages
                        WHERE user_id = %s
                    ''', (user_id,))

                    # Delete all chat histories associated with the user
                    cursor.execute('''
                        DELETE FROM chat_histories
                        WHERE user_id = %s
                    ''', (user_id,))
                print(
                    f'Deleted history and associated messages for user {user_id}')
        except Exception as e:
            logger.error(f"Error deleting all user histories: {e}")
            raise
        finally:
            self.release_connection(connection)

    def get_messages_for_chathistory(self, user_id, chat_history_id):
        connection = self.get_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute('''
                    SELECT * FROM messages
                    WHERE chat_history_id = %s AND user_id = %s
                ''', (chat_history_id, user_id))

                messages = cursor.fetchall()
                result_messages = []
                for message in messages:
                    result_messages.append({
                        'id': message[0],
                        'content': message[1],
                        'timestamp': message[2],
                        'user_id': message[3],
                        'chat_history_id': message[4],
                        'sender': message[5]
                    })
                return result_messages
        except Exception as e:
            logger.error(f"Error retrieving messages for chat history: {e}")
            raise
        finally:
            self.release_connection(connection)

    def format_user_chat_history(self, chat_history_id, user_id):
        # only last 5 messages
        connection = self.get_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute('''
                    SELECT content
                    FROM messages
                    WHERE chat_history_id = %s AND user_id = %s
                    ORDER BY timestamp DESC
                    LIMIT 5
                ''', (chat_history_id, user_id))

                rows = cursor.fetchall()

                chat_history = ""
                for row in rows:
                    content = row[0]
                    chat_history += content + "\n"  # Add newline for each message

                return chat_history.strip()  # Remove trailing newline
        except Exception as e:
            logger.error(f"Error formatting user chat history: {e}")
            raise
        finally:
            self.release_connection(connection)

    # User Files management methods
    def get_files_for_user(self, user_id):
        connection = self.get_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute('''
                    SELECT id, file_name, file_url
                    FROM files
                    WHERE user_id = %s
                ''', (user_id,))

                rows = cursor.fetchall()

                files = []
                for row in rows:
                    file_id = row[0]
                    file_info = {'id': file_id, 'name': row[1], 'publicUrl': row[2]}
                    file_info['processed'] = True
                    files.append(file_info)

                return files
        except Exception as e:
            logger.error(f"Error getting files for user: {e}")
            raise
        finally:
            self.release_connection(connection)

  

    def add_file(self, user_id, file_name, file_url):
        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute('''
                        INSERT INTO files (user_id, file_name, file_url)
                        VALUES (%s, %s, %s)
                        RETURNING id
                    ''', (user_id, file_name, file_url))

                    file_id = cursor.fetchone()[0]
                    return {'id': file_id, 'user_id': user_id, 'file_url': file_url}
        except Exception as e:
            logger.error(f"Error adding file: {e}")
            raise
        finally:
            self.release_connection(connection)

    def update_file_with_extract(self, user_id, file_name, extract):
        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    # Fetch the file ID
                    cursor.execute('''
                        SELECT id FROM files
                        WHERE user_id = %s AND file_name = %s
                    ''', (user_id, file_name))

                    file_id = cursor.fetchone()[0]

                    # Update the file's extract field
                    cursor.execute('''
                        UPDATE files
                        SET extract = %s
                        WHERE id = %s
                    ''', (extract, file_id))

        except Exception as e:
            logger.error(f"Error updating file with extract: {e}")
            raise
        finally:
            self.release_connection(connection)

    def get_file_extract(self, user_id, file_name):
        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    # Fetch the file ID
                    cursor.execute('''
                        SELECT id FROM files
                        WHERE user_id = %s AND file_name = %s
                    ''', (user_id, file_name))

                    file_id = cursor.fetchone()[0]

                    # Fetch the extract directly from the files table
                    cursor.execute('''
                        SELECT extract FROM files
                        WHERE id = %s
                    ''', (file_id,))

                    extract = cursor.fetchone()[0]

                    return extract

        except Exception as e:
            logger.error(f"Error retrieving file extract: {e}")
            raise
        finally:
            self.release_connection(connection)


    def delete_file_entry(self, user_id, file_id):
        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    # Fetch the file_name before deleting the entry
                    cursor.execute('''
                        SELECT file_name, id FROM files
                        WHERE user_id = %s AND id = %s
                    ''', (user_id, file_id))

                    result = cursor.fetchone()

                    if result:
                        file_name, file_id = result

                        # Simply delete the file entry now, as there are no chunks
                        cursor.execute('''
                            DELETE FROM files
                            WHERE user_id = %s AND id = %s
                        ''', (user_id, file_id))

                        return {'file_name': file_name, 'file_id': file_id}
                    else:
                        raise ValueError("File not found")
        except Exception as e:
            logger.error(f"Error deleting file entry: {e}")
            raise
        finally:
            self.release_connection(connection)

# Example usage:
if __name__ == "__main__":
    database_config = {
        'dbname': 'your_db_name',
        'user': 'your_db_user',
        'password': 'your_db_password',
        'host': 'your_db_host',
        'port': 'your_db_port'
    }
    store = DocSynthStore(database_config)

    # # Add a user and get the added user
    # added_user = store.add_user(
    #     'example@email.com', 'ExampleUser', is_subscribed=True)
    # print("Added User:", added_user)

    # # Add a chat history and get the added chat history
    # added_chat_history = store.add_chat_history(
    #     'Example Chat', user_id=added_user['id'])
    # print("Added Chat History:", added_chat_history)

    # # Add a message and get the added message
    # added_message = store.add_message(
    #     content="Hello, world!", user_id=added_user['id'], chat_history_id=added_chat_history['id'])
    # print("Added Message:", added_message)

    # Get all messages by a user
    user_messages = store.get_all_user_chat_histories(user_id=1)
    print("User Messages:", user_messages)

    # Get all messages in a chat history
    # chat_history_messages = store.get_chat_history_messages(
    #     chat_history_id=added_chat_history['id'])
    # print("Chat History Messages:", chat_history_messages)