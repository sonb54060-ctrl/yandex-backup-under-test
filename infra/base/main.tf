terraform {
  backend "local" {}
  required_version = ">= 1.10, < 2.0"
  required_providers {
    yandex = { source = "yandex-cloud/yandex", version = "0.228.0" }
    random = { source = "hashicorp/random", version = "~> 3.7" }
  }
}

provider "yandex" {
  cloud_id  = var.cloud_id
  folder_id = var.source_folder_id
  zone      = "ru-central1-b"
}

variable "cloud_id" { type = string }
variable "operator_account_id" {
  type        = string
  description = "IAM subject ID of the Terraform operator; retain management access in bucket policies."
}
variable "source_folder_id" { type = string }
variable "recovery_folder_id" {
  type = string
  validation {
    condition     = var.source_folder_id != var.recovery_folder_id
    error_message = "Use two separate test folders."
  }
}
variable "lab_id" {
  type    = string
  default = "backup-lab"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,18}$", var.lab_id))
    error_message = "lab_id must have 3–19 lowercase letters, digits or hyphens."
  }
}
variable "postgres_version" {
  type    = string
  default = "16"
}
variable "resource_preset_id" {
  type    = string
  default = "s3-c2-m8"
}
variable "disk_size_gb" {
  type    = number
  default = 10
}
variable "source_deletion_protection" {
  type    = bool
  default = true
}
variable "purge_synthetic_buckets" {
  type        = bool
  default     = false
  description = "Enable only for final teardown after saving reports; removes all demo object versions."
}

locals {
  zones   = { a = "ru-central1-a", b = "ru-central1-b", d = "ru-central1-d" }
  folders = { source = var.source_folder_id, recovery = var.recovery_folder_id }
  subnets = merge([
    for side, folder in local.folders : {
      for index, zone in ["a", "b", "d"] : "${side}-${zone}" => {
        side = side, folder = folder, zone = local.zones[zone]
        cidr = "10.${side == "source" ? 201 : 202}.${index}.0/24"
      }
    }
  ]...)
}

resource "random_id" "suffix" { byte_length = 4 }
resource "random_password" "database" {
  length  = 32
  special = false
}
resource "yandex_vpc_network" "lab" {
  for_each  = local.folders
  name      = "${var.lab_id}-${each.key}"
  folder_id = each.value
}
resource "yandex_vpc_subnet" "lab" {
  for_each       = local.subnets
  name           = "${var.lab_id}-${each.key}"
  folder_id      = each.value.folder
  network_id     = yandex_vpc_network.lab[each.value.side].id
  zone           = each.value.zone
  v4_cidr_blocks = [each.value.cidr]
}
resource "yandex_vpc_security_group" "postgres" {
  for_each   = local.folders
  name       = "${var.lab_id}-postgres-${each.key}"
  folder_id  = each.value
  network_id = yandex_vpc_network.lab[each.key].id
  ingress {
    protocol          = "ANY"
    predefined_target = "self_security_group"
  }
  ingress {
    protocol = "TCP"
    port     = 6432
    # VPC-connected functions use service subnets rather than user subnet IPs.
    v4_cidr_blocks = ["198.19.0.0/16"]
  }
  egress {
    protocol       = "ANY"
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "yandex_mdb_postgresql_cluster" "source" {
  name                = "${var.lab_id}-source"
  folder_id           = var.source_folder_id
  environment         = "PRODUCTION"
  network_id          = yandex_vpc_network.lab["source"].id
  security_group_ids  = [yandex_vpc_security_group.postgres["source"].id]
  deletion_protection = var.source_deletion_protection
  labels              = { "lab-id" = var.lab_id, "purpose" = "synthetic-source" }
  config {
    version = var.postgres_version
    resources {
      resource_preset_id = var.resource_preset_id
      disk_type_id       = "network-ssd"
      disk_size          = var.disk_size_gb
    }
    backup_retain_period_days = 7
  }
  host {
    zone             = "ru-central1-b"
    subnet_id        = yandex_vpc_subnet.lab["source-b"].id
    assign_public_ip = false
  }
}
resource "yandex_mdb_postgresql_user" "demo" {
  cluster_id = yandex_mdb_postgresql_cluster.source.id
  name       = "demo_owner"
  password   = random_password.database.result
}
resource "yandex_mdb_postgresql_database" "demo" {
  cluster_id = yandex_mdb_postgresql_cluster.source.id
  name       = "demo"
  owner      = yandex_mdb_postgresql_user.demo.name
}

resource "yandex_iam_service_account" "lab" {
  for_each  = toset(["setup", "controller", "workflow"])
  name      = "${var.lab_id}-${each.key}"
  folder_id = each.key == "setup" ? var.source_folder_id : var.recovery_folder_id
}
resource "yandex_resourcemanager_folder_iam_member" "controller_recovery" {
  for_each  = toset(["managed-postgresql.editor", "vpc.user", "logging.writer"])
  folder_id = var.recovery_folder_id
  role      = each.key
  member    = "serviceAccount:${yandex_iam_service_account.lab["controller"].id}"
}
resource "yandex_resourcemanager_folder_iam_member" "controller_restore" {
  folder_id = var.source_folder_id
  role      = "managed-postgresql.restorer"
  member    = "serviceAccount:${yandex_iam_service_account.lab["controller"].id}"
}
resource "yandex_resourcemanager_folder_iam_member" "setup" {
  for_each  = toset(["managed-postgresql.viewer", "vpc.user", "logging.writer"])
  folder_id = var.source_folder_id
  role      = each.key
  member    = "serviceAccount:${yandex_iam_service_account.lab["setup"].id}"
}
resource "yandex_resourcemanager_folder_iam_member" "workflow_logging" {
  folder_id = var.recovery_folder_id
  role      = "logging.writer"
  member    = "serviceAccount:${yandex_iam_service_account.lab["workflow"].id}"
}

resource "yandex_lockbox_secret" "s3" {
  for_each  = toset(["setup", "controller"])
  name      = "${var.lab_id}-${each.key}-s3"
  folder_id = var.recovery_folder_id
}
resource "yandex_iam_service_account_static_access_key" "s3" {
  for_each           = toset(["setup", "controller"])
  service_account_id = yandex_iam_service_account.lab[each.key].id
  output_to_lockbox {
    secret_id            = yandex_lockbox_secret.s3[each.key].id
    entry_for_access_key = "access_key"
    entry_for_secret_key = "secret_key"
  }
}
resource "yandex_lockbox_secret_iam_binding" "s3" {
  for_each  = toset(["setup", "controller"])
  secret_id = yandex_lockbox_secret.s3[each.key].id
  role      = "lockbox.payloadViewer"
  members   = ["serviceAccount:${yandex_iam_service_account.lab[each.key].id}"]
}
resource "yandex_lockbox_secret" "database" {
  name      = "${var.lab_id}-database"
  folder_id = var.recovery_folder_id
}
resource "yandex_lockbox_secret_version" "database" {
  secret_id = yandex_lockbox_secret.database.id
  entries {
    key        = "password"
    text_value = random_password.database.result
  }
}
resource "yandex_lockbox_secret_iam_binding" "database" {
  secret_id = yandex_lockbox_secret.database.id
  role      = "lockbox.payloadViewer"
  members   = [for key in ["setup", "controller"] : "serviceAccount:${yandex_iam_service_account.lab[key].id}"]
}

resource "yandex_storage_bucket" "attachments" {
  bucket        = "${var.lab_id}-${random_id.suffix.hex}-files"
  folder_id     = var.source_folder_id
  force_destroy = var.purge_synthetic_buckets
  versioning { enabled = true }
  anonymous_access_flags {
    read        = false
    list        = false
    config_read = false
  }
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Principal = { CanonicalUser = var.operator_account_id },
      Action = ["s3:*"], Resource = ["arn:aws:s3:::${var.lab_id}-${random_id.suffix.hex}-files", "arn:aws:s3:::${var.lab_id}-${random_id.suffix.hex}-files/*"] },
      { Effect = "Allow", Principal = { CanonicalUser = yandex_iam_service_account.lab["controller"].id },
      Action = ["s3:ListBucket"], Resource = "arn:aws:s3:::${var.lab_id}-${random_id.suffix.hex}-files" },
      { Effect = "Allow", Principal = { CanonicalUser = yandex_iam_service_account.lab["setup"].id },
        Action = ["s3:PutObject", "s3:GetObject", "s3:GetObjectVersion", "s3:DeleteObject", "s3:DeleteObjectVersion"],
      Resource = "arn:aws:s3:::${var.lab_id}-${random_id.suffix.hex}-files/demo/*" },
      { Effect = "Allow", Principal = { CanonicalUser = yandex_iam_service_account.lab["controller"].id },
      Action = ["s3:GetObjectVersion"], Resource = "arn:aws:s3:::${var.lab_id}-${random_id.suffix.hex}-files/demo/*" }
    ]
  })
  # Deliberately no expiration: retained DB checkpoints must never outlive their attachments.
}
resource "yandex_storage_bucket" "control" {
  bucket        = "${var.lab_id}-${random_id.suffix.hex}-control"
  folder_id     = var.recovery_folder_id
  force_destroy = var.purge_synthetic_buckets
  anonymous_access_flags {
    read        = false
    list        = false
    config_read = false
  }
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Principal = { CanonicalUser = var.operator_account_id },
      Action = ["s3:*"], Resource = ["arn:aws:s3:::${var.lab_id}-${random_id.suffix.hex}-control", "arn:aws:s3:::${var.lab_id}-${random_id.suffix.hex}-control/*"] },
      { Effect = "Allow", Principal = { CanonicalUser = [for key in ["setup", "controller"] : yandex_iam_service_account.lab[key].id] },
      Action = ["s3:ListBucket"], Resource = "arn:aws:s3:::${var.lab_id}-${random_id.suffix.hex}-control" },
      { Effect = "Allow", Principal = { CanonicalUser = yandex_iam_service_account.lab["controller"].id },
      Action = ["s3:GetObject", "s3:PutObject"], Resource = "arn:aws:s3:::${var.lab_id}-${random_id.suffix.hex}-control/*" },
      { Effect = "Allow", Principal = { CanonicalUser = yandex_iam_service_account.lab["setup"].id },
        Action = ["s3:GetObject", "s3:PutObject"], Resource = [
          "arn:aws:s3:::${var.lab_id}-${random_id.suffix.hex}-control/control/seed.json",
      "arn:aws:s3:::${var.lab_id}-${random_id.suffix.hex}-control/control/checkpoint.json"] }
    ]
  })
}

resource "yandex_storage_bucket_iam_binding" "files_setup" {
  bucket  = yandex_storage_bucket.attachments.id
  role    = "storage.editor"
  members = ["serviceAccount:${yandex_iam_service_account.lab["setup"].id}"]
}
resource "yandex_storage_bucket_iam_binding" "files_controller" {
  bucket  = yandex_storage_bucket.attachments.id
  role    = "storage.viewer"
  members = ["serviceAccount:${yandex_iam_service_account.lab["controller"].id}"]
}
resource "yandex_storage_bucket_iam_binding" "control_writers" {
  bucket  = yandex_storage_bucket.control.id
  role    = "storage.editor"
  members = [for key in ["setup", "controller"] : "serviceAccount:${yandex_iam_service_account.lab[key].id}"]
}

output "runtime_config" {
  value = {
    lab_id                     = var.lab_id, source_folder_id = var.source_folder_id, recovery_folder_id = var.recovery_folder_id,
    source_cluster_id          = yandex_mdb_postgresql_cluster.source.id,
    recovery_network_id        = yandex_vpc_network.lab["recovery"].id,
    recovery_subnet_id         = yandex_vpc_subnet.lab["recovery-b"].id,
    recovery_security_group_id = yandex_vpc_security_group.postgres["recovery"].id,
    attachments_bucket         = yandex_storage_bucket.attachments.id, control_bucket = yandex_storage_bucket.control.id,
    zone                       = "ru-central1-b", postgres_version = var.postgres_version, resource_preset_id = var.resource_preset_id,
    disk_size_gb               = var.disk_size_gb, database = "demo", database_user = "demo_owner",
    max_rows                   = 100, resource_ttl_seconds = 7200, restore_timeout_seconds = 3600, rto_target_seconds = 3600
  }
}
output "automation" {
  value = {
    source_folder_id    = var.source_folder_id, recovery_folder_id = var.recovery_folder_id, lab_id = var.lab_id,
    source_network_id   = yandex_vpc_network.lab["source"].id,
    recovery_network_id = yandex_vpc_network.lab["recovery"].id,
    service_accounts    = { for k, sa in yandex_iam_service_account.lab : k => sa.id },
    s3_secrets = { for k, secret in yandex_lockbox_secret.s3 : k => {
      id = secret.id, version = yandex_iam_service_account_static_access_key.s3[k].output_to_lockbox_version_id
    } },
    database_secret = { id = yandex_lockbox_secret.database.id, version = yandex_lockbox_secret_version.database.id }
  }
}
