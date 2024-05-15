from redis import StrictRedis
from redis_cache import RedisCache
import os

class CacheHandler:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            client = StrictRedis(host=os.environ.get(
                "CELERY_BROKER_HOST", "localhost"), decode_responses=True)
            cache = RedisCache(redis_client=client)
            cls._instance = super().__new__(cls)
            cls._instance.cache = cache
        return cls._instance
    
    def clear_cache(self):
        # Limpiar la cache
    	pass