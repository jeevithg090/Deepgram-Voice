from fastembed import TextEmbedding
import numpy as np

# Mock E-Commerce FAQ Knowledge Base
DOCUMENTS = [
    "Welcome to MockStore! We are open 24/7 online.",
    "Our return policy allows returns within 30 days of purchase with a valid receipt. Items must be in original condition.",
    "Refunds are processed to the original method of payment within 5-7 business days after we receive the returned item.",
    "If you want to exchange an item, please return the original item for a full refund and place a new order.",
    "Shipping takes 3-5 business days for standard delivery, and 1-2 days for express delivery.",
    "Shipping costs $5.99 for standard and $14.99 for express. Orders over $100 ship for free.",
    "You can track your order using the tracking link sent to your email after checkout.",
    "We accept all major credit cards (Visa, Mastercard, Amex), PayPal, and Apple Pay.",
    "For customer support, please email support@mockstore.com or call 1-800-MOCK-STORE.",
    "Yes, we ship internationally to over 50 countries. International shipping takes 7-14 business days.",
    "If you receive a damaged or defective item, please contact us within 48 hours for a free replacement.",
    "Members of our loyalty program get free shipping on all orders over $50 and exclusive discounts.",
    "To reset your password, click 'Forgot Password' on the login screen and follow the emailed instructions.",
    "We do not offer price matching at this time.",
    "Pre-ordered items will be shipped as soon as they become available in our warehouse.",
    "You can cancel your order within 1 hour of placing it by visiting your account dashboard.",
    "Gift cards are available in amounts ranging from $10 to $500 and never expire.",
    "You can apply promo codes at checkout in the 'Discount Code' box.",
    "Only one promo code can be used per order.",
    "We source our products sustainably and ensure fair trade practices.",
    "Subscribe to our newsletter to receive a 10% discount on your first order."
]

class KnowledgeBase:
    def __init__(self):
        print("Initializing FastEmbed Model...")
        # Using a small, fast model
        self.embedding_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        print("Generating embeddings for Knowledge Base...")
        
        # Generate embeddings for the documents
        embeddings_generator = self.embedding_model.embed(DOCUMENTS)
        self.embeddings = np.array(list(embeddings_generator))
        print("Knowledge Base ready!")

    def search(self, query: str, top_k: int = 2) -> str:
        # Embed the query
        query_embedding = list(self.embedding_model.embed([query]))[0]
        
        # Calculate cosine similarity
        scores = np.dot(self.embeddings, query_embedding) / (np.linalg.norm(self.embeddings, axis=1) * np.linalg.norm(query_embedding))
        
        # Get top_k results
        top_indices = np.argsort(scores)[::-1][:top_k]
        
        results = [DOCUMENTS[i] for i in top_indices]
        return "\n".join(results)

# Singleton instance
kb = KnowledgeBase()
