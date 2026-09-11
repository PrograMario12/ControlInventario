-- Base de datos separada para las pruebas automáticas (pytest), así nunca tocan tus datos reales.
-- Se ejecuta sólo la primera vez que se crea el volumen, como el usuario POSTGRES_USER (queda como dueño).
CREATE DATABASE inventario_test;
