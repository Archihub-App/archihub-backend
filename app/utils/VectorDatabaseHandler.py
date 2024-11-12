from qdrant_client import QdrantClient, models
from dotenv import load_dotenv
import os
load_dotenv()

VECTOR_HOST = os.environ.get('VECTOR_HOST', 'localhost')
VECTOR_PORT = os.environ.get('VECTOR_PORT', '6333')
VECTOR_SIZE = os.environ.get('VECTOR_SIZE', 768)

METADATA_RESOURCES = 'metadata_resources'
TRANSCRIPT_RECORDS = 'transcript_records'

class VectorDatabaseHandler:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.vector_host = VECTOR_HOST
            cls._instance.vector_port = VECTOR_PORT
            if os.environ.get('CELERY_WORKER'):
                from sentence_transformers import SentenceTransformer, util
                cls._instance.embedding_model = SentenceTransformer("jinaai/jina-embeddings-v2-base-es", trust_remote_code=True)
            cls._instance.qdrant = QdrantClient(host=cls._instance.vector_host, port=cls._instance.vector_port)
            
            if not cls._instance.qdrant.collection_exists(METADATA_RESOURCES):
                cls._instance.qdrant.create_collection(METADATA_RESOURCES, vectors_config=models.VectorParams(
                    size=VECTOR_SIZE,
                    distance=models.Distance.COSINE
                ))
            
            if not cls._instance.qdrant.collection_exists(TRANSCRIPT_RECORDS):
                cls._instance.qdrant.create_collection(TRANSCRIPT_RECORDS, vectors_config=models.VectorParams(
                    size=VECTOR_SIZE,
                    distance=models.Distance.COSINE
                ))
                
            collection_info = cls._instance.qdrant.get_collection(METADATA_RESOURCES)
            
            index_exists = any(index == 'id' for index in collection_info.payload_schema)

            if not index_exists:
                cls._instance.qdrant.create_payload_index(
                    collection_name=METADATA_RESOURCES,
                    field_name='id',
                    field_schema=models.PayloadSchemaType.KEYWORD
                )
            
        return cls._instance
    
    def insert_vector(self, collection, text, payload):
        vector = self.embedding_model.encode(text)
        self.qdrant.upsert(
            collection_name=collection,
            points=vector[0],
            payload=payload
        )
        
    def search_vector(self, collection, text, limit=5):
        vector = self.embedding_model.encode(text)
        return self.qdrant.search(
            collection_name=collection,
            query=vector[0],
            limit=limit,
            search_params=models.SearchRequest(
                hnsw_ef=128,
                exact=False,
                indexed_only=True,
            )
        )