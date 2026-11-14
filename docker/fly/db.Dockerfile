FROM mariadb:11.8.9
COPY docker/mariadb/init/ /docker-entrypoint-initdb.d/
COPY docker/mariadb/conf/wtm.cnf /etc/mysql/conf.d/wtm.cnf
# Fly's private network uses IPv6.
CMD ["mariadbd", "--bind-address=::"]
