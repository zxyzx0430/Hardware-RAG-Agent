import sys
sys.path.insert(0, 'backend')
from src.config.settings import settings
print('llm_model', settings.llm_model)
print('llm_base_url', settings.llm_base_url)
print('embedding_model', settings.embedding_model)
print('embedding_base_url', settings.embedding_base_url)
print('chroma_persist_dir', settings.chroma_persist_dir)
print('sqlite_db_path', settings.sqlite_db_path)
