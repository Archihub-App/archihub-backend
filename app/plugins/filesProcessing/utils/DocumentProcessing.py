from . import PDFprocessing
import subprocess
import os

def convert_to_pdf_with_libreoffice(input_file, output_dir):
    subprocess.run(['libreoffice', '--headless', '--convert-to', 'pdf', '--outdir', os.path.dirname(input_file), input_file])

def main(filepath, output_pdf, output):
    try:
        convert_to_pdf_with_libreoffice(filepath, output_pdf)
        PDFprocessing.main(output_pdf + '.pdf', output)

        return True
    except Exception as e:
        raise Exception('Error al convertir el archivo: ' + str(e))