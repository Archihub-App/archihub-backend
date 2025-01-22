settings = [
    {
        'name': 'post_types_settings',
        'label': 'Ajustes tipos de contenido',
        'data': [
            {
                'type': 'select',
                'label': 'Tipo por defecto del módulo de catalogación',
                'id': 'tipo_defecto'
            },
            {
                'type': 'checkbox',
                'label': 'Tipos a mostrar en la vista individual',
                'id': 'tipos_vista_individual'
            }
        ]
    },
    {
        'name': 'access_rights',
        'label': 'Roles y acceso a la información',
        'data': [
            {
                'type': 'select',
                'label': 'Listado para los niveles de acceso',
                'id': 'access_rights_list',
                'instructions': 'Los niveles de acceso definen la publicidad de la información. Por defecto, todo el contenido creado en la herramienta es Público. Se pueden crear nuevos niveles desde el apartado de lista en el módulo de catalogación y asignarlos a los usuarios para que estos puedan acceder a dicha información.'
            },
            {
                'type': 'select',
                'label': 'Listado para los roles de usuario',
                'id': 'user_roles_list',
                'instructions': 'Los roles de usuario definen la capacidad de los usuarios para manejar información. Por defecto existen admin y editor. Se pueden implementar nuevos roles para cada tipo de contenido.'
            }
        ]
    },
    {
        'name': 'active_plugins',
        'label': 'Plugins activos',
        'data': [],
        'plugins_settings': {}
    },
    {
        'name': 'api_activation',
        'label': 'API',
        'data': [
            {
                'type': 'checkbox_single',
                'label': 'Activar la API de administración',
                'id': 'api_activation_admin',
                'instructions': 'Activa la API para los endpoints de administración. La llave del administrador tiene una duración de no más de dos días y es importante desactivar la API una vez se hayan terminado de hacer los cambios.'
            },
            {
                'type': 'checkbox_single',
                'label': 'Activar la API pública',
                'id': 'api_activation_public',
                'instructions': 'Activa la API pública del aplicativo. Los endpoints públicos permiten consultar información y tienen un límite por semana por usuario.'
            }
        ]
    },
    {
        'name': 'index_management',
        'label': 'Administración de la búsqueda',
        'data': [
            {
                'type': 'checkbox_single',
                'label': 'Activar el índice para las búsquedas',
                'id': 'index_activation',
                'instructions': 'Activa la gestión del índice. Solo activar esta opción si ya se tiene la instalación de elasticsearch configurada.'
            },
            {
                'type': 'checkbox_single',
                'label': 'Activar la búsqueda semántica',
                'id': 'vector_activation',
                'instructions': 'Activa la base de datos vectorial para realizar búsqueda semánticas de la información. Es necesario tener QDrant configurado.'
            },
            {
                'type': 'button_single',
                'label': 'Regenerar el índice para la búsqueda de los recursos',
                'id': 'index_resources_remake',
                'instructions': 'Realizar esta acción si se acaba de activar el índice o si se modificaron los estándares de metadatos.',
                'btn_label': 'Regenerar índice'
            },
            {
                'type': 'button_single',
                'label': 'Volver a indexar los recursos',
                'id': 'index_resources_reindex',
                'instructions': 'Esta acción vuelve a indexar todos los recursos sobreescribiendo las versiones anteriores y creando las que no están.',
                'btn_label': 'Volver a indexar'
            }
        ]
    },
    {
        'name': 'cache_management',
        'label': 'Administración de la caché',
        'data': [
            {
                'type': 'button_single',
                'label': 'Limpiar la caché de la aplicación',
                'id': 'cache_clean',
                'instructions': 'Esta acción limpia la caché de la aplicación. Realizar esta acción si se han hecho cambios en la configuración de la aplicación.',
                'btn_label': 'Limpiar caché'
            }
        ]
    },
    {
        'name': 'geo_management',
        'label': 'Administración de la geolocalización',
        'data': [
            {
                'type': 'button_single',
                'label': 'Cargar polígonos geográficos',
                'id': 'geo_load',
                'instructions': 'Cargar los polígonos geográficos para la visualización de mapas en la aplicación.',
                'btn_label': 'Cargar polígonos'
            }
        ]
    },
    {
        'name': 'user_management',
        'label': 'Ajustes de usuario',
        'data': [
            {
                'type': 'checkbox_single',
                'label': 'Activar el registro de usuarios',
                'id': 'user_registration',
                'instructions': 'Activa el registro de usuarios en la aplicación. Los usuarios pueden registrarse y acceder a la información de la aplicación.'
            },
            {
                'type': 'checkbox_single',
                'label': 'Activar la recuperación de contraseña',
                'id': 'user_password_recovery',
                'instructions': 'Activa la recuperación de contraseña para los usuarios. Los usuarios pueden recuperar su contraseña en caso de olvido.'
            }
        ]
    },
]