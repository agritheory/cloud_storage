#!/bin/bash

set -e

DB="${DB:-mariadb}"

cd ~ || exit

sudo apt update
sudo apt remove mysql-server mysql-client
sudo apt install libcups2-dev redis-server mariadb-client

pip install frappe-bench

bench init --skip-assets --python "$(which python)" --frappe-branch version-15 frappe-bench

mkdir ~/frappe-bench/sites/test_site
if [ "$DB" == "postgres" ]; then
  cp "${GITHUB_WORKSPACE}/.github/helper/site_config_postgres.json" ~/frappe-bench/sites/test_site/site_config.json
  echo "travis" | psql -h 127.0.0.1 -p 5432 -c "CREATE DATABASE test_site" -U postgres
  echo "travis" | psql -h 127.0.0.1 -p 5432 -c "CREATE USER test_site WITH PASSWORD 'test_site'" -U postgres
  echo "travis" | psql -h 127.0.0.1 -p 5432 -c "GRANT ALL PRIVILEGES ON DATABASE test_site TO test_site" -U postgres
else
  cp "${GITHUB_WORKSPACE}/.github/helper/site_config.json" ~/frappe-bench/sites/test_site/site_config.json
fi


mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "SET GLOBAL character_set_server = 'utf8mb4'"
mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "SET GLOBAL collation_server = 'utf8mb4_unicode_ci'"
mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "CREATE USER 'test_frappe'@'localhost' IDENTIFIED BY 'test_frappe'"
mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "CREATE DATABASE test_frappe"
mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "GRANT ALL PRIVILEGES ON \`test_frappe\`.* TO 'test_frappe'@'localhost'"
mariadb --host 127.0.0.1 --port 3306 -u root -proot -e "FLUSH PRIVILEGES"

install_whktml() {
    wget -O /tmp/wkhtmltox.tar.xz https://github.com/frappe/wkhtmltopdf/raw/master/wkhtmltox-0.12.3_linux-generic-amd64.tar.xz
    tar -xf /tmp/wkhtmltox.tar.xz -C /tmp
    sudo mv /tmp/wkhtmltox/bin/wkhtmltopdf /usr/local/bin/wkhtmltopdf
    sudo chmod o+x /usr/local/bin/wkhtmltopdf
}
install_whktml &

cd ~/frappe-bench || exit

sed -i 's/watch:/# watch:/g' Procfile
sed -i 's/schedule:/# schedule:/g' Procfile
sed -i 's/socketio:/# socketio:/g' Procfile
sed -i 's/redis_socketio:/# redis_socketio:/g' Procfile

bench get-app erpnext --branch "version-15" --resolve-deps
bench get-app cloud_storage "${GITHUB_WORKSPACE}"
bench setup requirements --dev

bench start &> bench_run_logs.txt &
CI=Yes bench build --app frappe &

bench --site test_site reinstall --yes
bench --site test_site install-app cloud_storage
bench --site test_site execute 'cloud_storage.tests.setup.before_test'
