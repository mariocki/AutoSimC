#!/bin/bash


########
# This script ASSUMES Ubuntu to know if the correct packages are installed.
# Please update the script accordingly for your own distribution.
#######

echo "NOTE: Running this script for the first time is recommended as sudo/root incase any simc dependencies are missing."
echo "NOTE: If you already have the dependencies (build-essential libssl-dev) installed, you do not need root."

input=$1 # input file if not the normal one.
auto_simc_pwd=$(pwd) # current directory to get back to later.

if [[ -z $input ]]; then
    input=$auto_simc_pwd/input.simc # use the defualt input file if none specified.
fi

download_and_compile_simc() {
    mkdir -p ~/lgit/simc	# create a simc folder in the home dir
    cd ~/lgit/simc
    
    git pull
    
    echo "Compiling simc"
    cd engine
    make -j 8 OPENSSL=1 optimized # build from source
    
    # set the new path in the setting file.
    echo "Setting your simc executable in your settings_local.py"
    simc_location="r'$(pwd)/simc'" # get the executable location
    sed "s~simc_path = .*~simc_path = $simc_location~g" $auto_simc_pwd/settings_local.py > temp # update the settings file with the executable path.
    mv temp $auto_simc_pwd/settings_local.py # use a local settings file so we don't have to overwrite the original.
}

run_auto_simc() {
    echo "Running AutoSimC"
    cd $auto_simc_pwd
    
    python3 main.py -i $input
}

# prompt for installation.
download_and_compile_simc
# run auto simc
run_auto_simc
