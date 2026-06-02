import copy
import datetime
import hashlib
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from app.utils import DatabaseHandler


SKILL_COLLECTION = 'llm_skills'
INLINE_SKILL_PATTERN = re.compile(r'(?<!\S)\\([A-Za-z0-9_./-]+)')


class SkillManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.mongodb = DatabaseHandler.DatabaseHandler()
            cls._instance.skills_root = cls._instance._resolve_skills_root()
            cls._instance._started = False
        return cls._instance

    def _resolve_skills_root(self) -> str:
        configured_root = os.environ.get('LLM_SKILLS_PATH')
        if configured_root:
            return os.path.abspath(configured_root)

        app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        project_root = os.path.abspath(os.path.join(app_root, '..'))
        return os.path.join(project_root, 'skills')

    def start(self) -> None:
        if self._started:
            return
        os.makedirs(self.skills_root, exist_ok=True)
        self._started = True
        try:
            self.sync_from_filesystem()
        except Exception:
            self._started = False
            raise

    def get_root_path(self) -> str:
        return self.skills_root

    def sync_from_filesystem(self) -> List[Dict[str, Any]]:
        os.makedirs(self.skills_root, exist_ok=True)
        filesystem_entries = self._load_filesystem_entries()
        database_entries = self._load_database_entries()
        synced_paths = sorted(set(filesystem_entries.keys()) | set(database_entries.keys()))
        synced: List[Dict[str, Any]] = []

        for relative_path in synced_paths:
            filesystem_entry = filesystem_entries.get(relative_path)
            database_entry = database_entries.get(relative_path)

            if filesystem_entry and not database_entry:
                synced.append(self._sync_file_to_database(filesystem_entry['absolute_path'], relative_path))
                continue

            if database_entry and not filesystem_entry:
                synced.append(self._sync_database_record_to_file(database_entry))
                continue

            if not filesystem_entry or not database_entry:
                continue

            if filesystem_entry['content_hash'] == database_entry.get('content_hash'):
                synced.append(self._sync_file_to_database(filesystem_entry['absolute_path'], relative_path))
                continue

            database_updated_at = self._normalize_datetime(database_entry.get('updated_at'))
            filesystem_updated_at = filesystem_entry['updated_at']

            if filesystem_updated_at >= database_updated_at:
                synced.append(self._sync_file_to_database(filesystem_entry['absolute_path'], relative_path))
            else:
                synced.append(self._sync_database_record_to_file(database_entry))

        self._cleanup_empty_directories()
        return synced

    def list_skills(self, query: Optional[str] = None, include_content: bool = False, tree: bool = False) -> Any:
        self.start()
        filters: Dict[str, Any] = {'active': True}
        normalized_query = (query or '').strip()
        if normalized_query:
            regex = {'$regex': re.escape(normalized_query), '$options': 'i'}
            filters['$or'] = [
                {'path': regex},
                {'name': regex},
                {'title': regex},
                {'command': regex},
            ]

        fields = {
            'path': 1,
            'name': 1,
            'title': 1,
            'command': 1,
            'folder': 1,
            'content_hash': 1,
            'updated_at': 1,
            'created_at': 1,
            'active': 1,
        }
        if include_content:
            fields['content'] = 1

        records = list(self.mongodb.get_all_records(SKILL_COLLECTION, filters=filters, sort=[('path', 1)], fields=fields))
        items = [self._serialize_skill_record(record, include_content=include_content) for record in records]
        if tree:
            return self._build_tree(items)
        return items

    def get_skill(self, skill_path: str, include_content: bool = True) -> Optional[Dict[str, Any]]:
        self.start()
        normalized_path = self._normalize_skill_path(skill_path)
        if not normalized_path:
            return None

        record = self.mongodb.get_record(
            SKILL_COLLECTION,
            {'path': normalized_path, 'active': True},
            fields=self._build_record_fields(include_content=include_content),
        )
        if not record:
            return None
        return self._serialize_skill_record(record, include_content=include_content)

    def save_skill(self, skill_path: str, content: str) -> Dict[str, Any]:
        self.start()
        normalized_path = self._normalize_skill_path(skill_path)
        if not normalized_path:
            raise ValueError('Skill path is required')
        if not isinstance(content, str) or not content.strip():
            raise ValueError('Skill content is required')

        absolute_path = os.path.join(self.skills_root, normalized_path)
        os.makedirs(os.path.dirname(absolute_path), exist_ok=True)

        with open(absolute_path, 'w', encoding='utf-8') as skill_file:
            skill_file.write(content)

        return self._sync_file_to_database(absolute_path, normalized_path)

    def delete_skill(self, skill_path: str) -> bool:
        self.start()
        normalized_path = self._normalize_skill_path(skill_path)
        if not normalized_path:
            raise ValueError('Skill path is required')

        record = self.mongodb.get_record(
            SKILL_COLLECTION,
            {'path': normalized_path, 'active': True},
            fields={'path': 1},
        )
        absolute_path = os.path.join(self.skills_root, normalized_path)

        file_removed = False
        if os.path.exists(absolute_path):
            os.remove(absolute_path)
            file_removed = True

        if not record and not file_removed:
            return False

        now = datetime.datetime.utcnow()
        self.mongodb.update_record_operator(
            SKILL_COLLECTION,
            {'path': normalized_path},
            {'$set': {'active': False, 'updated_at': now}},
            upsert=False,
        )
        self._cleanup_empty_directories()
        return True

    def prepare_conversation_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        prepared_payload = dict(payload or {})
        message = prepared_payload.get('message')
        if not isinstance(message, str):
            return prepared_payload

        resolved = self.resolve_requested_skills(
            message=message,
            skill_ids=prepared_payload.get('skill_ids'),
            skill_paths=prepared_payload.get('skill_paths'),
            skill_names=prepared_payload.get('skill_names'),
            skills=prepared_payload.get('skills'),
        )

        prepared_payload['message'] = resolved['clean_message']
        prepared_payload['applied_skills'] = resolved['skills']
        prepared_payload['skill_paths'] = [skill['path'] for skill in resolved['skills']]
        prepared_payload['skill_context_applied'] = False
        return prepared_payload

    def resolve_requested_skills(
        self,
        message: Optional[str] = None,
        skill_ids: Optional[List[str]] = None,
        skill_paths: Optional[List[str]] = None,
        skill_names: Optional[List[str]] = None,
        skills: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        self.start()
        identifiers: List[str] = []
        for values in (skill_ids or [], skill_paths or [], skill_names or []):
            if isinstance(values, str):
                identifiers.append(values)
            elif isinstance(values, list):
                identifiers.extend(value for value in values if isinstance(value, str))

        if isinstance(skills, list):
            for item in skills:
                if isinstance(item, str):
                    identifiers.append(item)
                elif isinstance(item, dict):
                    for key in ('path', 'command', 'id', 'name', 'title'):
                        value = item.get(key)
                        if isinstance(value, str) and value.strip():
                            identifiers.append(value)
                            break

        inline_commands: List[str] = []
        clean_message = message or ''
        if isinstance(message, str) and message:
            inline_commands = [match.group(1) for match in INLINE_SKILL_PATTERN.finditer(message)]
            if inline_commands:
                clean_message = INLINE_SKILL_PATTERN.sub('', message)
                clean_message = re.sub(r'\s{2,}', ' ', clean_message).strip()

        identifiers.extend(inline_commands)

        resolved_skills: List[Dict[str, Any]] = []
        seen_paths = set()
        for identifier in identifiers:
            skill = self._lookup_skill(identifier)
            if not skill:
                continue
            skill_path = skill['path']
            if skill_path in seen_paths:
                continue
            seen_paths.add(skill_path)
            resolved_skills.append(skill)

        return {
            'clean_message': clean_message,
            'skills': resolved_skills,
            'inline_commands': inline_commands,
        }

    def enrich_messages(
        self,
        messages: Optional[List[Dict[str, Any]]],
        skill_ids: Optional[List[str]] = None,
        skill_paths: Optional[List[str]] = None,
        skill_names: Optional[List[str]] = None,
        skills: Optional[List[Any]] = None,
        skill_context_applied: bool = False,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        normalized_messages = copy.deepcopy(messages or [])
        if skill_context_applied or not normalized_messages:
            return normalized_messages, []

        last_user_index = None
        for index in range(len(normalized_messages) - 1, -1, -1):
            if normalized_messages[index].get('role') == 'user':
                last_user_index = index
                break

        if last_user_index is None:
            return normalized_messages, []

        user_message = normalized_messages[last_user_index]
        message_text = self._extract_message_text(user_message.get('content'))
        resolved = self.resolve_requested_skills(
            message=message_text,
            skill_ids=skill_ids,
            skill_paths=skill_paths,
            skill_names=skill_names,
            skills=skills,
        )
        if not resolved['skills']:
            return normalized_messages, []

        cleaned_content = self._apply_skill_context_to_content(
            user_message.get('content'),
            resolved['clean_message'],
            resolved['skills'],
        )
        user_message['content'] = cleaned_content
        return normalized_messages, resolved['skills']

    def _sync_file_to_database(self, absolute_path: str, relative_path: str) -> Dict[str, Any]:
        with open(absolute_path, 'r', encoding='utf-8') as skill_file:
            content = skill_file.read()

        now = datetime.datetime.utcnow()
        existing_record = self.mongodb.get_record(SKILL_COLLECTION, {'path': relative_path}, fields={'created_at': 1})
        payload = {
            'path': relative_path,
            'name': os.path.splitext(os.path.basename(relative_path))[0],
            'title': self._extract_title(relative_path, content),
            'command': self._build_command(relative_path),
            'folder': os.path.dirname(relative_path).replace('\\', '/').strip('/'),
            'content': content,
            'content_hash': hashlib.sha256(content.encode('utf-8')).hexdigest(),
            'active': True,
            'updated_at': now,
            'created_at': existing_record.get('created_at') if existing_record else now,
        }

        self.mongodb.update_record_operator(
            SKILL_COLLECTION,
            {'path': relative_path},
            {'$set': payload},
            upsert=True,
        )
        return self._serialize_skill_record(payload, include_content=True)

    def _sync_database_record_to_file(self, record: Dict[str, Any]) -> Dict[str, Any]:
        relative_path = self._normalize_skill_path(record.get('path'))
        if not relative_path:
            raise ValueError('Database skill path is required')

        absolute_path = os.path.join(self.skills_root, relative_path)
        os.makedirs(os.path.dirname(absolute_path), exist_ok=True)

        content = record.get('content') or ''
        with open(absolute_path, 'w', encoding='utf-8') as skill_file:
            skill_file.write(content)

        updated_at = self._normalize_datetime(record.get('updated_at'))
        updated_timestamp = updated_at.timestamp()
        os.utime(absolute_path, (updated_timestamp, updated_timestamp))

        return self._sync_file_to_database(absolute_path, relative_path)

    def _lookup_skill(self, identifier: str) -> Optional[Dict[str, Any]]:
        normalized_identifier = (identifier or '').strip().lstrip('\\').strip()
        if not normalized_identifier:
            return None

        candidates = [
            normalized_identifier,
            self._normalize_skill_path(normalized_identifier),
            self._build_command(self._normalize_skill_path(normalized_identifier) or normalized_identifier),
        ]
        deduped_candidates = []
        for candidate in candidates:
            if candidate and candidate not in deduped_candidates:
                deduped_candidates.append(candidate)

        or_filters = []
        for candidate in deduped_candidates:
            or_filters.extend([
                {'path': candidate},
                {'command': candidate},
                {'name': candidate},
                {'title': candidate},
            ])

        record = self.mongodb.get_record(
            SKILL_COLLECTION,
            {'active': True, '$or': or_filters},
            fields=self._build_record_fields(include_content=True),
        )
        if not record:
            return None
        return self._serialize_skill_record(record, include_content=True)

    def _normalize_skill_path(self, skill_path: Optional[str]) -> Optional[str]:
        if not isinstance(skill_path, str):
            return None
        normalized = skill_path.replace('\\', '/').strip().strip('/')
        if not normalized:
            return None
        normalized = os.path.normpath(normalized).replace('\\', '/')
        if normalized.startswith('..'):
            raise ValueError('Skill path must stay inside the skills directory')
        if not normalized.lower().endswith('.md'):
            normalized = f'{normalized}.md'
        return self._normalize_relative_path(normalized)

    def _normalize_relative_path(self, relative_path: str) -> str:
        normalized = os.path.normpath(relative_path).replace('\\', '/')
        return normalized.lstrip('./').strip('/')

    def _build_command(self, relative_path: str) -> str:
        normalized = self._normalize_relative_path(relative_path)
        if normalized.lower().endswith('.md'):
            normalized = normalized[:-3]
        return normalized

    def _extract_title(self, relative_path: str, content: str) -> str:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith('#'):
                return stripped.lstrip('#').strip() or os.path.splitext(os.path.basename(relative_path))[0]
        return os.path.splitext(os.path.basename(relative_path))[0]

    def _serialize_skill_record(self, record: Dict[str, Any], include_content: bool = False) -> Dict[str, Any]:
        serialized = {
            'id': record.get('path'),
            'path': record.get('path'),
            'name': record.get('name'),
            'title': record.get('title') or record.get('name'),
            'command': record.get('command') or self._build_command(record.get('path', '')),
            'folder': record.get('folder') or '',
            'updated_at': record.get('updated_at').isoformat() if hasattr(record.get('updated_at'), 'isoformat') else record.get('updated_at'),
            'created_at': record.get('created_at').isoformat() if hasattr(record.get('created_at'), 'isoformat') else record.get('created_at'),
            'content_hash': record.get('content_hash'),
        }
        if include_content:
            serialized['content'] = record.get('content', '')
        return serialized

    def _build_record_fields(self, include_content: bool = False) -> Dict[str, int]:
        fields = {
            'path': 1,
            'name': 1,
            'title': 1,
            'command': 1,
            'folder': 1,
            'content_hash': 1,
            'updated_at': 1,
            'created_at': 1,
        }
        if include_content:
            fields['content'] = 1
        fields['active'] = 1
        return fields

    def _load_filesystem_entries(self) -> Dict[str, Dict[str, Any]]:
        entries: Dict[str, Dict[str, Any]] = {}
        for root, dir_names, file_names in os.walk(self.skills_root):
            dir_names[:] = [name for name in dir_names if not name.startswith('.')]
            for file_name in sorted(file_names):
                if not file_name.lower().endswith('.md'):
                    continue
                absolute_path = os.path.join(root, file_name)
                relative_path = self._normalize_relative_path(
                    os.path.relpath(absolute_path, self.skills_root)
                )
                with open(absolute_path, 'r', encoding='utf-8') as skill_file:
                    content = skill_file.read()
                stat_result = os.stat(absolute_path)
                entries[relative_path] = {
                    'absolute_path': absolute_path,
                    'content': content,
                    'content_hash': hashlib.sha256(content.encode('utf-8')).hexdigest(),
                    'updated_at': datetime.datetime.fromtimestamp(stat_result.st_mtime, tz=datetime.timezone.utc),
                }
        return entries

    def _load_database_entries(self) -> Dict[str, Dict[str, Any]]:
        records = list(
            self.mongodb.get_all_records(
                SKILL_COLLECTION,
                filters={'active': True},
                sort=[('path', 1)],
                fields=self._build_record_fields(include_content=True),
            )
        )
        return {
            self._normalize_skill_path(record.get('path')): record
            for record in records
            if self._normalize_skill_path(record.get('path'))
        }

    def _normalize_datetime(self, value: Any) -> datetime.datetime:
        if isinstance(value, datetime.datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=datetime.timezone.utc)
            return value.astimezone(datetime.timezone.utc)
        return datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)

    def _cleanup_empty_directories(self) -> None:
        for root, dir_names, file_names in os.walk(self.skills_root, topdown=False):
            if root == self.skills_root:
                continue
            if dir_names or file_names:
                continue
            try:
                os.rmdir(root)
            except OSError:
                continue

    def _build_tree(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        tree: Dict[str, Dict[str, Any]] = {}
        roots: List[Dict[str, Any]] = []

        for item in items:
            folder_parts = [part for part in (item.get('folder') or '').split('/') if part]
            current_level = roots
            current_path = []
            for folder_part in folder_parts:
                current_path.append(folder_part)
                folder_key = '/'.join(current_path)
                node = tree.get(folder_key)
                if not node:
                    node = {
                        'type': 'folder',
                        'name': folder_part,
                        'path': folder_key,
                        'children': [],
                    }
                    tree[folder_key] = node
                    current_level.append(node)
                current_level = node['children']

            current_level.append({
                'type': 'skill',
                **item,
            })

        return roots

    def _extract_message_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get('type') == 'text' and isinstance(item.get('text'), str):
                    parts.append(item.get('text'))
            return '\n'.join(parts).strip()
        return ''

    def _apply_skill_context_to_content(self, content: Any, clean_message: str, skills: List[Dict[str, Any]]) -> Any:
        skill_context = self._render_skill_context(skills)
        if isinstance(content, str):
            clean_user_message = clean_message.strip() if isinstance(clean_message, str) else ''
            if clean_user_message:
                return f'{skill_context}\n\nUser request:\n{clean_user_message}'
            return skill_context

        if isinstance(content, list):
            updated_content = copy.deepcopy(content)
            skill_prefix = f'{skill_context}\n\n'
            for item in updated_content:
                if isinstance(item, dict) and item.get('type') == 'text' and isinstance(item.get('text'), str):
                    current_text = item.get('text', '')
                    item['text'] = skill_prefix + ((clean_message or current_text).strip() if isinstance(clean_message, str) and clean_message.strip() else current_text)
                    return updated_content

            updated_content.append({
                'type': 'text',
                'text': skill_prefix + (clean_message.strip() if isinstance(clean_message, str) else ''),
            })
            return updated_content

        return content

    def _render_skill_context(self, skills: List[Dict[str, Any]]) -> str:
        skill_sections = []
        for skill in skills:
            title = skill.get('title') or skill.get('name') or skill.get('path')
            command = skill.get('command') or self._build_command(skill.get('path', ''))
            content = (skill.get('content') or '').strip()
            skill_sections.append(
                f'Active skill: {title}\\{command}\n{content}'
            )

        joined_sections = '\n\n'.join(skill_sections)
        return (
            'Use the following skill instructions as additional context for this request. '
            'Follow them only when they are relevant and do not override higher-priority safety or system rules.\n\n'
            f'{joined_sections}'
        )