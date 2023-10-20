import shutil
from pdf2image import convert_from_path
from PIL import Image
import os

def main(path, output_path):
    try:
        output = output_path + '/web/big'
        if not os.path.exists(output):
            os.makedirs(output)
        
        images = convert_from_path(path, output_folder=output, fmt='jpg', output_file="page_")
        
        output = output_path + '/web/small'
        if not os.path.exists(output):
            os.makedirs(output)
        
        for image in images:
            image.thumbnail((100, 100))
            image.save(output + '/' + os.path.splitext(os.path.basename(image.filename))[0] + '.jpg', "JPEG")
        return True
    except Exception as e:
        raise Exception('Error al convertir el archivo: ' + str(e))
