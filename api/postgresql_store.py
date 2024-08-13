import psycopg2
from psycopg2 import pool, sql, OperationalError, errors
from datetime import datetime
import json
import os
from llm_service import get_text_embedding
import pickle
from scipy.spatial.distance import cosine
from tfidf_helper import TfIdfHelper
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DocSynthStore:
    def __init__(self, database_config):
        self.database_config = database_config
        self.vectorizer = TfidfVectorizer()
        self.pool = psycopg2.pool.SimpleConnectionPool(1, 20, **database_config)
        self.create_tables()

    def get_connection(self):
        try:
            return self.pool.getconn()
        except OperationalError as e:
            logger.error(f"Error getting connection from pool: {e}")
            raise

    def release_connection(self, connection):
        self.pool.putconn(connection)

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
                            stripe_subscription_id TEXT NOT NULL,
                            status TEXT NOT NULL,
                            user_id INTEGER,
                            FOREIGN KEY (user_id) REFERENCES users (id)
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
                            is_liked BOOLEAN DEFAULT FALSE,
                            is_disliked BOOLEAN DEFAULT FALSE,
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
                            FOREIGN KEY (user_id) REFERENCES users (id)
                        )
                    ''')

                    # Table to store page metadata
                    cursor.execute('''
                        CREATE TABLE IF NOT EXISTS pages (
                            id SERIAL PRIMARY KEY,
                            file_id INTEGER,
                            page_number INTEGER,
                            data TEXT,
                            FOREIGN KEY (file_id) REFERENCES files (id)
                        )
                    ''')

                    # Table to store chunks and their vectors
                    cursor.execute('''
                        CREATE TABLE IF NOT EXISTS chunks (
                            id SERIAL PRIMARY KEY,
                            page_id INTEGER,
                            chunk TEXT,
                            embedding_vector BYTEA,
                            FOREIGN KEY (page_id) REFERENCES pages (id)
                        )
                    ''')
        except Exception as e:
            logger.error(f"Error creating tables: {e}")
        finally:
            self.release_connection(connection)

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
            with connection.cursor() as cursor:
                cursor.execute('''
                    SELECT id FROM users WHERE email = %s
                ''', (email,))
                result = cursor.fetchone()
            return result[0] if result else None
        except Exception as e:
            logger.error(f"Error getting user ID from email: {e}")
            raise
        finally:
            self.release_connection(connection)

    def add_or_update_subscription(self, user_id, stripe_customer_id, stripe_subscription_id, status):
        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    # Check if the subscription already exists for the user
                    cursor.execute('''
                        SELECT id FROM subscriptions
                        WHERE user_id = %s AND stripe_subscription_id = %s
                    ''', (user_id, stripe_subscription_id))
                    existing_subscription = cursor.fetchone()

                    if existing_subscription:
                        # If the subscription exists, update the status
                        subscription_id = existing_subscription[0]
                        cursor.execute('''
                            UPDATE subscriptions
                            SET status = %s
                            WHERE stripe_subscription_id = %s
                        ''', (status, stripe_subscription_id))
                    else:
                        # If the subscription doesn't exist, add a new entry
                        cursor.execute('''
                            INSERT INTO subscriptions (user_id, stripe_customer_id, stripe_subscription_id, status)
                            VALUES (%s, %s, %s, %s)
                            RETURNING id
                        ''', (user_id, stripe_customer_id, stripe_subscription_id, status))
                        subscription_id = cursor.fetchone()[0]

                    # Commit the transaction
                    connection.commit()

                    # Return subscription details
                    return {'id': subscription_id, 'user_id': user_id, 'stripe_customer_id': stripe_customer_id, 'stripe_subscription_id': stripe_subscription_id, 'status': status}
        except Exception as e:
            logger.error(f"Error adding or updating subscription: {e}")
            raise
        finally:
            self.release_connection(connection)

    def get_subscription(self, user_id):
        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute('''
                        SELECT * FROM subscriptions
                        WHERE user_id = %s
                    ''', (user_id,))

                    result = cursor.fetchone()

                    # If a subscription exists for the user, return the subscription object
                    if result:
                        subscription = {
                            'id': result[0],
                            'stripe_customer_id': result[1],
                            'stripe_subscription_id': result[2],
                            'status': result[3],
                            'user_id': result[4]
                            # Add more fields as needed
                        }
                        return subscription
                    else:
                        return None
        except Exception as e:
            logger.error(f"Error getting subscription: {e}")
            raise
        finally:
            self.release_connection(connection)

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

    def add_message(self, content, sender, user_id, chat_history_id, is_liked=False, is_disliked=False):
        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute('''
                        INSERT INTO messages (content, sender, user_id, chat_history_id, is_liked, is_disliked)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING id, timestamp
                    ''', (content, sender, user_id, chat_history_id, is_liked, is_disliked))

                    message_id, timestamp = cursor.fetchone()

                    return {
                        'id': message_id,
                        'content': content,
                        'sender': sender,
                        'timestamp': timestamp,
                        'user_id': user_id,
                        'chat_history_id': chat_history_id,
                        'is_liked': is_liked,
                        'is_disliked': is_disliked
                    }
        except Exception as e:
            logger.error(f"Error adding message: {e}")
            raise
        finally:
            self.release_connection(connection)

    def like_message(self, message_id, user_id):
        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute('''
                        UPDATE messages
                        SET is_liked = TRUE
                        WHERE id = %s AND user_id = %s
                    ''', (message_id, user_id))

                    if cursor.rowcount == 0:
                        raise ValueError(
                            "Message not found or user does not have permission to like the message")
        except Exception as e:
            logger.error(f"Error liking message: {e}")
            raise
        finally:
            self.release_connection(connection)

    def dislike_message(self, message_id, user_id):
        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute('''
                        UPDATE messages
                        SET is_disliked = TRUE
                        WHERE id = %s AND user_id = %s
                    ''', (message_id, user_id))

                    if cursor.rowcount == 0:
                        raise ValueError(
                            "Message not found or user does not have permission to dislike the message")
        except Exception as e:
            logger.error(f"Error disliking message: {e}")
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
                    SELECT id, content, sender, timestamp, is_liked, is_disliked, user_id
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
                        'is_liked': row[4],
                        'is_disliked': row[5],
                        'user_id': row[6],
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
                        'is_liked': message[5],
                        'is_disliked': message[6],
                        'sender': message[7]
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

                    # Check if the file has chunks and pages
                    cursor.execute('''
                        SELECT COUNT(*) FROM pages
                        WHERE file_id = %s
                    ''', (file_id,))
                    pages_count = cursor.fetchone()[0]

                    cursor.execute('''
                        SELECT COUNT(*) FROM chunks
                        WHERE page_id IN (SELECT id FROM pages WHERE file_id = %s)
                    ''', (file_id,))
                    chunks_count = cursor.fetchone()[0]

                    file_info['processed'] = (pages_count > 0 and chunks_count > 0)
                    files.append(file_info)

                return files
        except Exception as e:
            logger.error(f"Error getting files for user: {e}")
            raise
        finally:
            self.release_connection(connection)

    def get_tfidf_vectors(self, documents):
        return self.vectorizer.fit_transform(documents)

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

    def update_file_with_chunks(self, user_id, file_name, doc_info):
        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute('''
                        SELECT id FROM files
                        WHERE user_id = %s AND file_name = %s
                    ''', (user_id, file_name))

                    file_id = cursor.fetchone()[0]

                    # Add pages and chunks to the file
                    for entry in doc_info:
                        page_number = entry['page_number']
                        data = entry['data']
                        chunks = entry['chunks']

                        cursor.execute('''
                            INSERT INTO pages (file_id, page_number, data)
                            VALUES (%s, %s, %s)
                            RETURNING id
                        ''', (file_id, page_number, data))

                        page_id = cursor.fetchone()[0]

                        for chunk in chunks:
                            # Embedding vector
                            embedding_vector = get_text_embedding(chunk)
                            embedding_vector_blob = pickle.dumps(embedding_vector)

                            cursor.execute('''
                                INSERT INTO chunks (page_id, chunk, embedding_vector)
                                VALUES (%s, %s, %s)
                            ''', (page_id, chunk, embedding_vector_blob))

        except Exception as e:
            logger.error(f"Error updating file with chunks: {e}")
            raise
        finally:
            self.release_connection(connection)

    def knn_search(self, query, user_id, k=5):
        query_embedding_vector = get_text_embedding(query)

        connection = self.get_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute('''
                    SELECT c.id, c.page_id, c.chunk, c.embedding_vector
                    FROM chunks c
                    JOIN pages p ON c.page_id = p.id
                    JOIN files f ON p.file_id = f.id
                    WHERE f.user_id = %s
                ''', (user_id,))

                results = cursor.fetchall()

                if not results:
                    return []

                embedding_similarities = []
                for result in results:
                    chunk_id, page_id, chunk, embedding_vector_blob = result
                    embedding_vector = pickle.loads(embedding_vector_blob)

                    # Calculate similarity for embeddings
                    embedding_similarity = 1 - cosine(query_embedding_vector, embedding_vector)
                    embedding_similarities.append((chunk_id, page_id, chunk, embedding_similarity))

                # Sort by similarity (highest to lowest)
                embedding_similarities.sort(key=lambda x: x[3], reverse=True)

                # Get top-k results
                top_k_embeddings = embedding_similarities[:k]

                return top_k_embeddings
        except Exception as e:
            logger.error(f"Error performing KNN search: {e}")
            raise
        finally:
            self.release_connection(connection)

    def tfidf_search(self, query, user_id, k=5):
        # Retrieve documents and their text from the database
        connection = self.get_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute('''
                    SELECT c.id, c.page_id, c.chunk
                    FROM chunks c
                    JOIN pages p ON c.page_id = p.id
                    JOIN files f ON p.file_id = f.id
                    WHERE f.user_id = %s
                ''', (user_id,))

                results = cursor.fetchall()

                if not results:
                    return []

                # Extract chunks of text for TF-IDF vectorization
                chunks = [result[2] for result in results]

                # Compute TF-IDF vectors for the documents
                tfidf_matrix = self.get_tfidf_vectors(chunks)

                # Compute TF-IDF vector for the query
                query_vector = self.vectorizer.transform([query])

                # Compute cosine similarities between query and documents
                similarities = cosine_similarity(query_vector, tfidf_matrix).flatten()

                # Combine results with their similarities
                chunk_similarities = [(results[i][0], results[i][1], results[i][2], similarities[i]) for i in range(len(results))]

                # Sort by similarity (highest to lowest)
                chunk_similarities.sort(key=lambda x: x[3], reverse=True)

                # Get top-k results
                top_k_similarities = chunk_similarities[:k]

                return top_k_similarities
        except Exception as e:
            logger.error(f"Error performing TF-IDF search: {e}")
            raise
        finally:
            self.release_connection(connection)

    def hybrid_search(self, query, user_id, k=5):
        # Perform KNN search
        knn_results = self.knn_search(query, user_id, k)
        knn_results = {(result[0], result[1]): (result[2], result[3], 'knn') for result in knn_results}

        # Perform TF-IDF search
        tfidf_results = self.tfidf_search(query, user_id, k)
        tfidf_results = {(result[0], result[1]): (result[2], result[3], 'tfidf') for result in tfidf_results}

        # Combine and deduplicate results
        combined_results = {}
        for result_dict in [knn_results, tfidf_results]:
            for key, value in result_dict.items():
                if key not in combined_results:
                    combined_results[key] = value
                else:
                    # Merge scores from different methods if needed
                    combined_results[key] = (
                        combined_results[key][0],
                        max(combined_results[key][1], value[1]),
                        combined_results[key][2]
                    )

        # Sort by the highest similarity score
        sorted_results = sorted(combined_results.items(), key=lambda x: x[1][1], reverse=True)

        # Get top-k results
        top_k_results = sorted_results[:k]

        # Fetch page data for combined results
        final_results = []
        connection = self.get_connection()
        try:
            with connection.cursor() as cursor:
                for (chunk_id, page_id), (chunk_text, similarity, source) in top_k_results:
                    cursor.execute('''
                        SELECT p.file_id, p.page_number, p.data, f.file_url
                        FROM pages p
                        JOIN files f ON p.file_id = f.id
                        WHERE p.id = %s
                    ''', (page_id,))
                    page_data = cursor.fetchone()
                    if page_data:
                        file_id, page_number, data, file_url = page_data
                        final_results.append({
                            "chunk_id": chunk_id,
                            "page_id": page_id,
                            "chunk": chunk_text,
                            "file_id": file_id,
                            "page_number": page_number,
                            "data": data,
                            "similarity": similarity,
                            "file_url": file_url,
                            "source": source
                        })
        except Exception as e:
            logger.error(f"Error performing hybrid search: {e}")
            raise
        finally:
            self.release_connection(connection)

        return final_results

    def delete_file_entry(self, user_id, file_id):
        connection = self.get_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    # Fetch the file_name and vector_doc_id before deleting the entry
                    cursor.execute('''
                        SELECT file_name, id FROM files
                        WHERE user_id = %s AND id = %s
                    ''', (user_id, file_id))

                    result = cursor.fetchone()

                    if result:
                        file_name, file_id = result

                        # First, delete all chunks associated with the pages of the file
                        cursor.execute('''
                            DELETE FROM chunks
                            WHERE page_id IN (
                                SELECT id FROM pages WHERE file_id = %s
                            )
                        ''', (file_id,))

                        # Then, delete all pages associated with the file
                        cursor.execute('''
                            DELETE FROM pages
                            WHERE file_id = %s
                        ''', (file_id,))

                        # Then delete the file entry
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
