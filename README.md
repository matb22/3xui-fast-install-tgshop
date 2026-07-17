<h1>УСТАНОВКА :</h1>
<h2>
  1)git clone https://github.com/matb22/3xui-fast-install-tgshop.git   
  <h3>
    # Переходим в нашу папку
    
    cd 3xui-fast-install-tgshop
  </h3>
</h2>


<h2>
  2)Настройка окружения .env
  <h3>
    # Копируем шаблон в рабочий файл конфигурации
    
    cp .env.save .env
  </h3>
  <h3>
    # Открываем файл для редактирования (например, через nano)
    
    nano .env
  </h3>
  
</h2>
<h2>
  3)Развертывание через Docker Compose
  <h3>
    # Для сборки и запуска бота в фоновом режиме выполните команду:

    docker compose up -d --build
  </h3>
</h2>
<h2>
  Полезные команды для администрирования:
  <h3>
    # Просмотр логов бота в реальном времени:

    docker compose logs -f  
  </h3>
  <h3>
    # Остановка бота:
    
    docker compose down
  </h3>
  
  
</h2>
