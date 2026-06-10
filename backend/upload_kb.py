import os
import uuid
from pinecone import Pinecone

# Load config or use hardcoded values
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY", "pcsk_38Vngg_T8xMuCctVSZTHsPk7hAoF4K61bT4ZmTW1Rs2ZPT8oTVzdkqg7T8N8YnLFhRspXt")
PINECONE_HOST = "https://sellix-knowledge-base-ltve4fd.svc.aped-4627-b74a.pinecone.io"

def upload_to_pinecone(text_content):
    print(f"Connecting to Pinecone...")
    pc = Pinecone(api_key=PINECONE_API_KEY)
    
    print(f"Generating embeddings for the text...")
    # 'passage' is used for the documents we are storing, while 'query' is used for searching
    embed_data = pc.inference.embed(
        model="multilingual-e5-large", 
        inputs=[text_content],
        parameters={"input_type": "passage"} 
    )
    
    vector = embed_data[0].values
    vector_id = str(uuid.uuid4())
    
    print(f"Uploading to index...")
    index = pc.Index(host=PINECONE_HOST)
    
    index.upsert(
        vectors=[{
            "id": vector_id,
            "values": vector,
            "metadata": {"text": text_content}
        }]
    )
    
    print(f"Successfully uploaded! Vector ID: {vector_id}")

if __name__ == "__main__":
    print("--- Sellix Knowledge Base Uploader ---")
    print("Paste the text you want to upload (press Ctrl+D on Unix or Ctrl+Z on Windows then Enter to finish):")
    
    import sys
    document_text = sys.stdin.read().strip()
    
    if not document_text:
        print("No text provided. Exiting.")
    else:
        upload_to_pinecone(document_text)