from sklearn.feature_extraction.text import TfidfVectorizer
import numpy as np
import pickle

class TfIdfHelper:

    def __init__(self):
        self.vectorizer = TfidfVectorizer()
        self.tfidf_matrix = None

    def fit_tfidf(self, documents):
        self.tfidf_matrix = self.vectorizer.fit_transform(documents)

    def get_tfidf_vector(self, document):
        return self.vectorizer.transform([document])