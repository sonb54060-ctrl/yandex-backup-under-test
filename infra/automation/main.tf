terraform {
  backend "local" {}
  required_version = ">= 1.10, < 2.0"
  required_providers {
    yandex = { source = "yandex-cloud/yandex", version = "0.228.0" }
  }
}
variable "cloud_id" { type = string }
variable "base_output_file" { type = string }
variable "function_archive" { type = string }
variable "enable_daily_schedule" {
  type        = bool
  default     = false
  description = "Enable daily runs. To disable an existing schedule, also run yc serverless workflow update ID --remove-schedule before applying false (provider Optional+Computed)."
}
locals {
  base = jsondecode(file(var.base_output_file))
  functions = {
    setup      = { sa = "setup", entrypoint = "backup_lab.fixture.handler", side = "source" }
    controller = { sa = "controller", entrypoint = "backup_lab.handler.handler", side = "recovery" }
    janitor    = { sa = "controller", entrypoint = "backup_lab.janitor_handler.handler", side = "recovery" }
  }
}
provider "yandex" {
  cloud_id  = var.cloud_id
  folder_id = local.base.recovery_folder_id
}
resource "yandex_function" "lab" {
  for_each           = local.functions
  name               = "${local.base.lab_id}-${each.key}"
  folder_id          = each.value.side == "source" ? local.base.source_folder_id : local.base.recovery_folder_id
  runtime            = "python312"
  entrypoint         = each.value.entrypoint
  memory             = 256
  execution_timeout  = 90
  concurrency        = 1
  user_hash          = filesha256(var.function_archive)
  service_account_id = local.base.service_accounts[each.value.sa]
  connectivity {
    network_id = each.value.side == "source" ? local.base.source_network_id : local.base.recovery_network_id
  }
  content { zip_filename = var.function_archive }
  dynamic "secrets" {
    for_each = { AWS_ACCESS_KEY_ID = "access_key", AWS_SECRET_ACCESS_KEY = "secret_key" }
    content {
      id                   = local.base.s3_secrets[each.value.sa].id
      version_id           = local.base.s3_secrets[each.value.sa].version
      key                  = secrets.value
      environment_variable = secrets.key
    }
  }
  secrets {
    id                   = local.base.database_secret.id
    version_id           = local.base.database_secret.version
    key                  = "password"
    environment_variable = "DB_PASSWORD"
  }
}
resource "yandex_function_iam_binding" "invoke" {
  for_each    = toset(["controller", "janitor"])
  function_id = yandex_function.lab[each.key].id
  role        = "functions.functionInvoker"
  members     = ["serviceAccount:${local.base.service_accounts.workflow}"]
}
resource "yandex_serverless_workflow" "drill" {
  name               = "${local.base.lab_id}-drill"
  folder_id          = local.base.recovery_folder_id
  service_account_id = local.base.service_accounts.workflow
  is_public          = false
  specification = {
    spec_yaml = templatefile("${path.module}/../../workflows/drill.yaml.tftpl", {
      function_id = yandex_function.lab["controller"].id
    })
  }
  schedule = var.enable_daily_schedule ? {
    cron_expression = "0 3 * * * *"
    timezone        = "UTC"
  } : null
  # Optional+Computed: clear an existing schedule explicitly through yc before applying false.
  # The API also refuses to delete a workflow while its schedule exists.
  depends_on = [yandex_function_iam_binding.invoke]
}
resource "yandex_serverless_triggers" "janitor" {
  name      = "${local.base.lab_id}-janitor"
  folder_id = local.base.recovery_folder_id
  source = {
    timer = { cron_expression = "*/15 * * * ? *" }
  }
  action = [{
    invoke_function = {
      function_id        = yandex_function.lab["janitor"].id
      service_account_id = local.base.service_accounts.workflow
    }
    retry_policy = { retry_attempts = 2, interval = "30s" }
  }]
  depends_on = [yandex_function_iam_binding.invoke]
}
output "function_ids" { value = { for k, f in yandex_function.lab : k => f.id } }
output "workflow_id" { value = yandex_serverless_workflow.drill.id }
