import sqlite3
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

class DocSynthStore:
    def __init__(self, database_path):
        self.database_path = database_path
        self.vectorizer = TfidfVectorizer()
        self.connection = sqlite3.connect(
            database_path, check_same_thread=False)
        self.cursor = self.connection.cursor()
        self.create_tables()
       
    def create_tables(self):
        with sqlite3.connect(self.database_path, check_same_thread=False) as connection:
            cursor = connection.cursor()

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    username TEXT NOT NULL UNIQUE
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY,
                    stripe_customer_id TEXT NOT NULL,
                    stripe_subscription_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    user_id INTEGER,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS chat_histories (
                    id INTEGER PRIMARY KEY,
                    title TEXT,
                    user_id INTEGER,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY,
                    content TEXT,
                    sender TEXT,
                    is_liked BOOLEAN DEFAULT FALSE,
                    is_disliked BOOLEAN DEFAULT FALSE,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    user_id INTEGER,
                    chat_history_id INTEGER,
                    FOREIGN KEY (user_id) REFERENCES users (id),
                    FOREIGN KEY (chat_history_id) REFERENCES chat_histories (id)
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER,
                    file_name TEXT,
                    file_url TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            ''')

            # Table to store page metadata
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS pages (
                    id INTEGER PRIMARY KEY,
                    file_id INTEGER,
                    page_number INTEGER,
                    data TEXT,
                    FOREIGN KEY (file_id) REFERENCES files (id)
                )
            ''')

            # Table to store chunks and their vectors
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY,
                    page_id INTEGER,
                    chunk TEXT,
                    embedding_vector BLOB,
                    FOREIGN KEY (page_id) REFERENCES pages (id)
                )
            ''')



    
    def add_user(self, email, username):
        with sqlite3.connect(self.database_path, check_same_thread=False) as connection:
            cursor = connection.cursor()
            try:
                cursor.execute('''
                    INSERT INTO users (email, username)
                    VALUES (?, ?)
                ''', (email, username))
                user_id = cursor.lastrowid
                return {'id': user_id, 'email': email, 'username': username}
            except sqlite3.IntegrityError as e:
                if "UNIQUE constraint failed" in str(e):
                    existing_user_cursor = connection.cursor()
                    existing_user_cursor.execute('''
                        SELECT * FROM users WHERE email = ? OR username = ?
                    ''', (email, username))
                    existing_user = existing_user_cursor.fetchone()
                    existing_user_cursor.close()

                    if existing_user:
                        return {'id': existing_user[0], 'email': existing_user[1], 'username': existing_user[2]}
                    else:
                        raise ValueError("Error: Existing user not found.")
                else:
                    raise e

    def get_user_id_from_email(self, email):
        with sqlite3.connect(self.database_path, check_same_thread=False) as connection:
            cursor = connection.cursor()
            cursor.execute('''
                SELECT id FROM users
                WHERE email = ?
            ''', (email,))

            user_id = cursor.fetchone()

            return user_id[0] if user_id else None

    def add_or_update_subscription(self, user_id, stripe_customer_id, stripe_subscription_id, status):
        try:
            # Establish a connection and create a cursor within the context manager
            with sqlite3.connect(self.database_path, check_same_thread=False) as connection:
                cursor = connection.cursor()

                # Check if the subscription already exists for the user
                cursor.execute('''
                    SELECT id FROM subscriptions
                    WHERE user_id = ? AND stripe_subscription_id = ?
                ''', (user_id, stripe_subscription_id))
                existing_subscription = cursor.fetchone()

                if existing_subscription:
                    # If the subscription exists, update the status
                    subscription_id = existing_subscription[0]
                    cursor.execute('''
                        UPDATE subscriptions
                        SET status = ?
                        WHERE stripe_subscription_id = ?
                    ''', (status, stripe_subscription_id))
                else:
                    # If the subscription doesn't exist, add a new entry
                    cursor.execute('''
                        INSERT INTO subscriptions (user_id, stripe_customer_id, stripe_subscription_id, status)
                        VALUES (?, ?, ?, ?)
                    ''', (user_id, stripe_customer_id, stripe_subscription_id, status))
                    subscription_id = cursor.lastrowid

                # Commit the transaction
                connection.commit()

                # Return subscription details
                return {'id': subscription_id, 'user_id': user_id, 'stripe_customer_id': stripe_customer_id, 'stripe_subscription_id': stripe_subscription_id, 'status': status}
        except sqlite3.Error as e:
            raise ValueError(f"Error adding or updating subscription: {e}")

    def get_subscription(self, user_id):
        with self.connection:
            self.cursor.execute('''
                SELECT * FROM subscriptions
                WHERE user_id = ?
            ''', (user_id,))

            result = self.cursor.fetchone()

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

    def add_chat_history(self, title, user_id):
        with self.connection:
            self.cursor.execute('''
                INSERT INTO chat_histories (title, user_id)
                VALUES (?, ?)
            ''', (title, user_id))

            chat_history_id = self.cursor.lastrowid
            return {'id': chat_history_id, 'title': title, 'user_id': user_id}

    def add_message(self, content, sender, user_id, chat_history_id, is_liked=False, is_disliked=False):
        with self.connection:
            self.cursor.execute('''
                INSERT INTO messages (content, sender, user_id, chat_history_id, is_liked, is_disliked)
                VALUES (?, ?, ?, ?, ?,?)
            ''', (content, sender, user_id, chat_history_id, is_liked, is_disliked))

            message_id = self.cursor.lastrowid
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

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

    def like_message(self, message_id, user_id):
        with self.connection:
            self.cursor.execute('''
                UPDATE messages
                SET is_liked = TRUE
                WHERE id = ? AND user_id = ?
            ''', (message_id, user_id))

            if self.cursor.rowcount == 0:
                raise ValueError(
                    "Message not found or user does not have permission to like the message")

    def dislike_message(self, message_id, user_id):
        with self.connection:
            self.cursor.execute('''
                UPDATE messages
                SET is_disliked = TRUE
                WHERE id = ? AND user_id = ?
            ''', (message_id, user_id))

            if self.cursor.rowcount == 0:
                raise ValueError(
                    "Message not found or user does not have permission to dislike the message")

    def get_all_user_chat_histories(self, user_id):
        try:
            with sqlite3.connect(self.database_path, check_same_thread=False) as connection:
                cursor = connection.cursor()

                cursor.execute('''
                    SELECT id, title
                    FROM chat_histories
                    WHERE user_id = ?
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
        except sqlite3.Error as e:
            print(f"Error retrieving chat histories: {e}")
            return []

    def get_messages_for_chat_history(self, chat_history_id, user_id):
        with self.connection:
            self.cursor.execute('''
                SELECT id, content, sender, timestamp, is_liked, is_disliked, user_id
                FROM messages
                WHERE chat_history_id = ? AND user_id = ?
            ''', (chat_history_id, user_id))

            rows = self.cursor.fetchall()

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

    def delete_chat_history(self, user_id, history_id):
        with self.connection:
            # Delete messages associated with the chat history
            self.cursor.execute('''
                DELETE FROM messages
                WHERE chat_history_id = ? AND user_id = ?
            ''', (history_id, user_id))

            # Delete the chat history
            self.cursor.execute('''
                DELETE FROM chat_histories
                WHERE id = ? AND user_id = ?
            ''', (history_id, user_id))
        print(
            f'Deleted history {history_id} and associated messages for user {user_id}')

    def delete_all_user_histories(self, user_id):
        with self.connection:
            # Delete all messages associated with the user
            self.cursor.execute('''
                DELETE FROM messages
                WHERE user_id = ?
            ''', (user_id,))

            # Delete all chat histories associated with the user
            self.cursor.execute('''
                DELETE FROM chat_histories
                WHERE user_id = ?
            ''', (user_id,))
            print(
                f'Deleted history and associated messages for user {user_id}')

    def get_messages_for_chathistory(self, user_id, chat_history_id):
        with self.connection:
            self.cursor.execute('''
                SELECT * FROM messages
                WHERE chat_history_id = ? AND user_id = ?
            ''', (chat_history_id, user_id))

            messages = self.cursor.fetchall()
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

    def format_user_chat_history(self, chat_history_id, user_id):
        # only last 5 messages
        with self.connection:
            self.cursor.execute('''
                SELECT content
                FROM messages
                WHERE chat_history_id = ? AND user_id = ?
                ORDER BY timestamp DESC
                LIMIT 5 
            ''', (chat_history_id, user_id))

            rows = self.cursor.fetchall()

            chat_history = ""
            for row in rows:
                content = row[0]
                chat_history += content + "\n"  # Add newline for each message

            return chat_history.strip()  # Remove trailing newline
    
    

    def get_files_for_user(self, user_id):
        with self.connection:
            self.cursor.execute('''
                SELECT id, file_name, file_url
                FROM files
                WHERE user_id = ?
            ''', (user_id,))

            rows = self.cursor.fetchall()

            files = []
            for row in rows:
                file_info = {'id': row[0], 'name': row[1],
                             'publicUrl': row[2]}
                files.append(file_info)

            return files
    
    def get_tfidf_vectors(self, documents):
        return self.vectorizer.fit_transform(documents)

    def add_file(self, user_id, file_name, file_url, document_chunks):
        with self.connection:
            cursor = self.connection.cursor()
            cursor.execute('''
                INSERT INTO files (user_id, file_name, file_url)
                VALUES (?, ?, ?)
            ''', (user_id, file_name, file_url))

            file_id = cursor.lastrowid

            # Gather all chunks for TF-IDF fitting
            all_chunks = []

            for entry in document_chunks:
                page_number = entry['page_number']
                data = entry['data']
                chunks = entry['chunks']

                cursor.execute('''
                    INSERT INTO pages (file_id, page_number, data)
                    VALUES (?, ?, ?)
                ''', (file_id, page_number, data))

                page_id = cursor.lastrowid

                for chunk in chunks:
                    all_chunks.append(chunk)

                    # Embedding vector
                    embedding_vector = get_text_embedding(chunk)
                    embedding_vector_blob = pickle.dumps(embedding_vector)

                    cursor.execute('''
                        INSERT INTO chunks (page_id, chunk, embedding_vector)
                        VALUES (?, ?, ?)
                    ''', (page_id, chunk, embedding_vector_blob))

            

            return {'id': file_id, 'user_id': user_id, 'file_url': file_url}

    def knn_search(self, query, user_id, k=5):
        query_embedding_vector = get_text_embedding(query)

        with self.connection:
            cursor = self.connection.cursor()
            cursor.execute('''
                SELECT c.id, c.page_id, c.chunk, c.embedding_vector
                FROM chunks c
                JOIN pages p ON c.page_id = p.id
                JOIN files f ON p.file_id = f.id
                WHERE f.user_id = ?
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

    def tfidf_search(self, query, user_id, k=5):
        # Retrieve documents and their text from the database
        with self.connection:
            cursor = self.connection.cursor()
            cursor.execute('''
                SELECT c.id, c.page_id, c.chunk
                FROM chunks c
                JOIN pages p ON c.page_id = p.id
                JOIN files f ON p.file_id = f.id
                WHERE f.user_id = ?
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
        with self.connection:
            cursor = self.connection.cursor()
            for (chunk_id, page_id), (chunk_text, similarity, source) in top_k_results:
                cursor.execute('''
                    SELECT p.file_id, p.page_number, p.data, f.file_url
                    FROM pages p
                    JOIN files f ON p.file_id = f.id
                    WHERE p.id = ?
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

        return final_results


    def delete_file_entry(self, user_id, file_id):
        with self.connection:
            cursor = self.connection.cursor()

            # Fetch the file_name and vector_doc_id before deleting the entry
            cursor.execute('''
                SELECT file_name, id FROM files
                WHERE user_id = ? AND id = ?
            ''', (user_id, file_id))

            result = cursor.fetchone()

            if result:
                file_name, file_id = result

                # First, delete all chunks associated with the pages of the file
                cursor.execute('''
                    DELETE FROM chunks
                    WHERE page_id IN (
                        SELECT id FROM pages WHERE file_id = ?
                    )
                ''', (file_id,))

                # Then, delete all pages associated with the file
                cursor.execute('''
                    DELETE FROM pages
                    WHERE file_id = ?
                ''', (file_id,))

                # Then delete the file entry
                cursor.execute('''
                    DELETE FROM files
                    WHERE user_id = ? AND id = ?
                ''', (user_id, file_id))

                return {'file_name': file_name, 'file_id': file_id}
            else:
                raise ValueError("File not found")

   
    
# Example usage:
if __name__ == "__main__":
    database_path = 'docsynth.db'
    store = DocSynthStore(database_path)

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
