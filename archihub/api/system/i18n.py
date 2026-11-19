"""Translations for system settings metadata.

The system collection stores settings groups with metadata (group labels,
descriptions, field labels, instructions, button labels, and select options).
These documents may be seeded in Spanish or English; when returned by the API
(``GET /system``), every human-facing string is rendered in the active locale
so administrators see a fully-localised settings interface.
"""

from __future__ import annotations

import copy
import logging

from archihub.core.i18n import get_locale

logger = logging.getLogger(__name__)

#: Translations from Spanish (as stored in Mongo defaults) to English.
SETTINGS_ES_TO_EN: dict[str, str] = {
    # Group labels & descriptions
    "Ajustes tipos de contenido": "Content type settings",
    "Roles y acceso a la información": "Roles and information access",
    "Niveles de acceso y configuración de roles": "Access levels and role configuration",
    "Plugins activos": "Active plugins",
    "API": "API",
    "Administración de la búsqueda": "Search management",
    "Administración de la caché": "Cache management",
    "Administración de la geolocalización": "Geolocation management",
    "Ajustes de usuario": "User settings",
    "Ajustes de los archivos": "File settings",
    "Reinicio del sistema": "System restart",

    # Content type settings
    "Tipo por defecto del módulo de catalogación": "Default cataloguing module type",
    "Tipos a mostrar en la vista individual": "Types to show in detail view",

    # Access rights settings
    "Activar la visualización de metadatos para todos los usuarios": "Enable metadata display for all users",
    "Activar esta opción habilita los metadatos para todos los usuarios, de lo contrario los metadatos solo serán visibles para los usuarios con permisos": (
        "Enabling this option makes metadata visible to all users; otherwise metadata will only be visible to users with permissions"
    ),
    "Roles de usuario": "User roles",
    "Configuración de los roles de usuario y sus permisos de acceso a las diferentes secciones del sistema.": (
        "Configure user roles and their access permissions to different sections of the system."
    ),
    "Gestionar permisos": "Manage permissions",

    # API settings
    "Activar la API de administración": "Enable administration API",
    "Activa la API para los endpoints de administración. La llave del administrador tiene una duración de no más de dos días y es importante desactivar la API una vez se hayan terminado de hacer los cambios.": (
        "Enables the API for administration endpoints. The administrator key lasts no more than two days and it is important to deactivate the API once changes are complete."
    ),
    "Activar la API pública": "Enable public API",
    "Activa la API pública del aplicativo. Los endpoints públicos permiten consultar información y tienen un límite por semana por usuario.": (
        "Enables the public API of the application. Public endpoints allow querying information and have a weekly limit per user."
    ),

    # Search management settings
    "Activar el índice para las búsquedas": "Enable search indexing",
    "Activa la gestión del índice. Solo activar esta opción si ya se tiene la instalación de elasticsearch configurada.": (
        "Enables index management. Only activate this option if Elasticsearch is already configured."
    ),
    "Activar la búsqueda semántica": "Enable semantic search",
    "Activa la base de datos vectorial para realizar búsqueda semánticas de la información. Es necesario tener QDrant configurado.": (
        "Enables the vector database to perform semantic searches of information. Qdrant must be configured."
    ),
    "Regenerar el índice para la búsqueda de los recursos": "Regenerate resource search index",
    "Realizar esta acción si se acaba de activar el índice o si se modificaron los estándares de metadatos.": (
        "Perform this action if the index was just enabled or if metadata standards were modified."
    ),
    "Regenerar índice": "Regenerate index",
    "Volver a indexar los recursos": "Reindex resources",
    "Esta acción vuelve a indexar todos los recursos sobreescribiendo las versiones anteriores y creando las que no están.": (
        "This action reindexes all resources, overwriting previous versions and creating missing ones."
    ),
    "Volver a indexar": "Reindex",
    "Regenerar índice de polígonos geográficos": "Regenerate geographic polygon index",
    "Indexar polígonos geográficos": "Index geographic polygons",
    "Esta acción vuelve a indexar todos los polígonos geográficos sobreescribiendo las versiones anteriores y creando las que no están.": (
        "This action reindexes all geographic polygons, overwriting previous versions and creating missing ones."
    ),
    "Volver a indexar polígonos": "Reindex polygons",

    # Cache management settings
    "Limpiar la caché de la aplicación": "Clear application cache",
    "Esta acción limpia la caché de la aplicación. Realizar esta acción si se han hecho cambios en la configuración de la aplicación.": (
        "This action clears the application cache. Perform this action if application configuration changes have been made."
    ),
    "Limpiar caché": "Clear cache",

    # Geolocation settings
    "Cargar polígonos geográficos": "Load geographic polygons",
    "Cargar los polígonos geográficos para la visualización de mapas en la aplicación.": (
        "Load geographic polygons for map visualization in the application."
    ),
    "Cargar polígonos": "Load polygons",

    # User management settings
    "Activar el registro de usuarios": "Enable user registration",
    "Activa el registro de usuarios en la aplicación. Los usuarios pueden registrarse y acceder a la información de la aplicación.": (
        "Enables user registration in the application. Users can register and access application information."
    ),
    "Activar la recuperación de contraseña": "Enable password recovery",
    "Activa la recuperación de contraseña para los usuarios. Los usuarios pueden recuperar su contraseña en caso de olvido.": (
        "Enables password recovery for users. Users can recover their password if forgotten."
    ),
    "Idioma por defecto de los usuarios": "Default user language",
    "Idioma del sistema": "System language",
    "Seleccionar el idioma por defecto para los usuarios de la aplicación.": (
        "Select the default language for application users."
    ),
    "Seleccionar el idioma de la aplicación (español o inglés).": (
        "Select the application language (Spanish or English)."
    ),
    "Español": "Spanish",
    "Inglés": "English",

    # Files management settings
    "Activar la descarga de archivos": "Enable file downloads",
    "Activa la descarga de archivos en la aplicación. Los usuarios pueden descargar los archivos de la aplicación.": (
        "Enables file downloads in the application. Users can download application files."
    ),
    "Borrar archivos zipeados": "Delete zipped files",
    "Borrar los archivos zipeados que se han generado en la aplicación.": (
        "Delete zipped files generated by the application."
    ),
    "Borrar inventarios públicos": "Delete public inventories",
    "Borrar los inventarios públicos que se han generado en la aplicación.": (
        "Delete public inventories generated by the application."
    ),
    "Borrar archivos": "Delete files",

    # System restart settings
    "Reiniciar el sistema": "Restart system",
    "Reinicia el sistema para aplicar cambios de configuración.": (
        "Restarts the system to apply configuration changes."
    ),
    "Reiniciar backend": "Restart backend",
}

#: Reverse dictionary: English to Spanish.
SETTINGS_EN_TO_ES: dict[str, str] = {en: es for es, en in SETTINGS_ES_TO_EN.items()}


def translate_text(text: str | None, target_locale: str) -> str | None:
    """Translate a settings metadata string into the target locale.

    Preserves unrecognised strings unchanged so custom or plugin-defined
    settings are never corrupted or blanked.
    """
    if not text or not isinstance(text, str):
        return text

    if target_locale == "en":
        return SETTINGS_ES_TO_EN.get(text, text)
    if target_locale == "es":
        return SETTINGS_EN_TO_ES.get(text, text)
    return text


def translate_system_settings(
    settings: list[dict], locale: str | None = None
) -> list[dict]:
    """Translate system settings groups for rendering in the admin interface.

    Leaves database records untouched: operates on a deep copy and translates
    group labels, descriptions, data item labels, instructions, button labels,
    and select option labels.
    """
    active_locale = locale or get_locale()
    translated_settings = copy.deepcopy(settings)

    for group in translated_settings:
        if not isinstance(group, dict):
            continue

        if "label" in group:
            group["label"] = translate_text(group["label"], active_locale)
        if "description" in group:
            group["description"] = translate_text(group["description"], active_locale)

        data = group.get("data")
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                if "label" in item:
                    item["label"] = translate_text(item["label"], active_locale)
                if "instructions" in item:
                    item["instructions"] = translate_text(item["instructions"], active_locale)
                if "btn_label" in item:
                    item["btn_label"] = translate_text(item["btn_label"], active_locale)
                options = item.get("options")
                if isinstance(options, list):
                    for opt in options:
                        if isinstance(opt, dict) and "label" in opt:
                            opt["label"] = translate_text(opt["label"], active_locale)

    return translated_settings

