from PIL import Image

def main(filepath, output):
    try:
        im = Image.open(filepath)

        w = im.size[0]
        h = im.size[1]

        copy = im.copy()
        if copy.mode in ("RGBA", "P"):
            copy = copy.convert("RGB")
        copy.thumbnail((2500,2500))
        copy.save(output + '_large.jpg', 'JPEG', optimize=True, quality=90)

        copy = im.copy()
        if copy.mode in ("RGBA", "P"):
            print("Tiene canal alpha")
            copy = copy.convert("RGB")
        copy.thumbnail((1100,1100))
        copy.save(output + '_medium.jpg', 'JPEG', optimize=True, quality=80)

        copy = im.copy()
        if copy.mode in ("RGBA", "P"):
            print("Tiene canal alpha")
            copy = copy.convert("RGB")
        copy.thumbnail((72,72))
        copy.save(output + '_small.jpg', 'JPEG', optimize=True, quality=60)

        return True
    except Exception as e:
        raise Exception('Error al convertir el archivo: ' + str(e))