import shutil
from pdf2image import convert_from_path
from PIL import Image
import os
import pypdf

def clean_pdf(file_path):
    try:
        reader = pypdf.PdfReader(file_path)
        writer = pypdf.PdfWriter()

        for page in reader.pages:
            writer.add_page(page)

        writer.remove_javascript()

        with open(file_path, "wb") as f:
            writer.write(f)
        return True
    except Exception as e:
        raise Exception('Error al convertir el archivo: ' + str(e))

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
