import pandas as pd
import shutil

def main_csv(filepath, output):
    try:
        print(filepath)
        df = pd.DataFrame(pd.read_csv(filepath))
        df.head(99).to_csv(output + "_min.csv", 
                        index = False,
                        header=True)

        # move file using shutil
        shutil.copy(filepath, output + '.csv')

        return True
    except Exception as e:
        raise Exception('Error al convertir el archivo: ' + str(e))
    
def main_excel(filepath, output):
    try:
        print(filepath)
        df = pd.DataFrame(pd.read_excel(filepath))
        df.to_csv(output + ".csv", 
                        index = False,
                        header=True)
        df.head(99).to_csv(output + "_min.csv", 
                        index = False,
                        header=True)

        return True
    except Exception as e:
        raise Exception('Error al convertir el archivo: ' + str(e))