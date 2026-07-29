import pyvips
import exiftool

def main(filepath, output):
    try:
        # 1. Extraer metadatos intactos
        metadata_list = None
        with exiftool.ExifToolHelper() as et:
            metadata_list = et.get_metadata([filepath])

        # 2. Generar las versiones planas usando "shrink-on-load"
        # pyvips.Image.thumbnail es increíblemente rápido y ahorra RAM
        img_large = pyvips.Image.thumbnail(filepath, 2500)
        img_large.write_to_file(output + '_large.jpg', Q=90, optimize_coding=True)

        img_medium = pyvips.Image.thumbnail(filepath, 1100)
        img_medium.write_to_file(output + '_medium.jpg', Q=80, optimize_coding=True)

        img_small = pyvips.Image.thumbnail(filepath, 110)
        img_small.write_to_file(output + '_small.jpg', Q=80, optimize_coding=True)

        # 3. Evaluar el sistema de Tiles
        # Cargamos la imagen en modo secuencial (bajo consumo de memoria)
        image = pyvips.Image.new_from_file(filepath, access='sequential')
        max_dim = max(image.width, image.height)

        is_dzi = False
        if max_dim >= 4096:
            # dzsave genera un archivo .dzi y una carpeta "_files" con los tiles
            # Ej: output_tiles.dzi y output_tiles_files/
            image.dzsave(output + '_tiles')
            is_dzi = True

        return True, metadata_list[0] if metadata_list else None, is_dzi
    except Exception as e:
        raise Exception('Error al convertir el archivo: ' + str(e))