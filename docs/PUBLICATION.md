# Публикация

Исходники: [yandex-backup-under-test](https://github.com/sonb54060-ctrl/yandex-backup-under-test) в отдельном авторском репозитории.

Правки документации подготовлены в ветке `contrib/backup-under-test` от актуального `yandex-cloud/docs`, коммит `5228495525074412a96e60fc6e5773c495f0c634`. Добавляются две страницы, общий текст руководства и шесть ссылок навигации.

Обе страницы собраны в HTML командой `yfm content --strict` (Diplodoc 5.61.1); YAML оглавлений разобран, новые ссылки проверены. Полная сборка всех страниц репозитория не выполнялась. Подробные результаты испытаний находятся в [VALIDATION.md](VALIDATION.md) и [CLOUD-RUN.md](CLOUD-RUN.md).

Для таймера уборки используется `yandex_serverless_triggers` (API v2). Ежедневное расписание Workflows по умолчанию выключено.

Предложение темы подготовлено в [ISSUE.md](ISSUE.md). Категорию `new-solution`, актуальность темы и размер гранта определяют редакторы. Готовая реализация и результаты тестов не означают одобрение материала программой.

Тема отправлена редакторам: [issue #1232](https://github.com/yandex-cloud/docs/issues/1232). Правки опубликованы как [Draft PR #1233](https://github.com/yandex-cloud/docs/pull/1233). Редакционное согласование ожидается.

[GitHub Actions](https://github.com/sonb54060-ctrl/yandex-backup-under-test/actions/runs/35645673965) успешно проверил Python и Terraform для версии `676326702f2d2d261d4589b94b2fd3a5470e1bf9`.
