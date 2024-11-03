import requests
import argparse

# Banner
print("""
 ██████████                              █████████  █████                        █████                        
░░███░░░░███                            ███░░░░░███░░███                        ░░███                         
 ░███   ░░███  ██████  █████████████   ███     ░░░  ░███████    ██████   ██████  ░███ █████  ██████  ████████ 
 ░███    ░███ ███░░███░░███░░███░░███ ░███          ░███░░███  ███░░███ ███░░███ ░███░░███  ███░░███░░███░░███
 ░███    ░███░███ ░███ ░███ ░███ ░███ ░███          ░███ ░███ ░███████ ░███ ░░░  ░██████░  ░███████  ░███ ░░░ 
 ░███    ███ ░███ ░███ ░███ ░███ ░███ ░░███     ███ ░███ ░███ ░███░░░  ░███  ███ ░███░░███ ░███░░░   ░███     
 ██████████  ░░██████  █████░███ █████ ░░█████████  ████ █████░░██████ ░░██████  ████ █████░░██████  █████    
░░░░░░░░░░    ░░░░░░  ░░░░░ ░░░ ░░░░░   ░░░░░░░░░  ░░░░ ░░░░░  ░░░░░░   ░░░░░░  ░░░░ ░░░░░  ░░░░░░  ░░░░░     
                                                                                                              
By: El Pingüino de Mario                                                                                                              

https://elrincondelhacker.es               
""")

def comprobar_dominio(dominio):
    try:
        response = requests.get(f"http://{dominio}", timeout=5)
        # Aquí vemos el código de estado y lo guardamos en la variable response.
        return response.status_code == 200
    except requests.RequestException:
        return False

def main(domains_file):
    with open(domains_file, 'r') as file:
        dominios = file.readlines()

    print("\033[93mDominios operativos:\033[0m")  # Texto amarillito fosforito
    for dominio in dominios:
        dominio = dominio.strip()  # Método strip para quitar espacios
        if comprobar_dominio(dominio): # Vamos pasando cada uno de los dominios a la función encargada de ir comprobando cada uno de ellos :)
            print(dominio)  

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Comprobar dominios operativos.')
    parser.add_argument('domains_file', type=str, help='Archivo de texto con los dominios a comprobar.')
    args = parser.parse_args()

    main(args.domains_file)

