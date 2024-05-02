from PIL import Image

def main(filepath, output):
    try:
        im = Image.open(filepath)

        w = im.size[0]
        h = im.size[1]

        copy = im.copy()
        if copy.mode in ("RGBA", "P"):
            copy = copy.convert("RGB")
        copy.thumbnail((2000,2000))
        copy.save(output + '_large.jpg', 'JPEG', optimize=True, quality=80)

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
        copy.thumbnail((700,700))
        copy.save(output + '_small.jpg', 'JPEG', optimize=True, quality=80)

        return True
    except Exception as e:
        raise Exception('Error al convertir el archivo: ' + str(e))