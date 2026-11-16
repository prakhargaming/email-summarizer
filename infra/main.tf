terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

locals {
  region     = "us-east-2"
  ssm_prefix = "/email-triage/prod/"
  # Secrets: real values are set with `aws ssm put-parameter --overwrite`, never through Terraform state.
  secure_params = ["DATABASE_URL", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "TOKEN_ENCRYPTION_KEY", "LLM_API_KEY", "NGROK_AUTHTOKEN"]
  string_params = {
    LLM_BASE_URL   = "https://gateway.ngrok.ai"
    LLM_MODEL      = "CHANGE_ME"
    ALLOWED_EMAILS = "prakhar530@gmail.com,nguyen26.bn@gmail.com"
    ENV            = "prod"
  }
}

provider "aws" {
  region = local.region
  default_tags {
    tags = { project = "email-triage" }
  }
}

data "aws_caller_identity" "me" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-arm64-server-*"]
  }
}

resource "aws_security_group" "box" {
  name        = "email-triage"
  description = "No inbound: ngrok and SSM Session Manager connect outbound only"
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_iam_role" "box" {
  name = "email-triage-ec2"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.box.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "read_params" {
  name = "read-email-triage-params"
  role = aws_iam_role.box.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
        # GetParametersByPath is authorized against the path itself, the others against each parameter.
        Resource = [
          "arn:aws:ssm:${local.region}:${data.aws_caller_identity.me.account_id}:parameter/email-triage/prod",
          "arn:aws:ssm:${local.region}:${data.aws_caller_identity.me.account_id}:parameter/email-triage/prod/*",
        ]
      },
      {
        # The AWS-managed aws/ssm key only exists after the first SecureString, so scope by service, not key id.
        Effect    = "Allow"
        Action    = "kms:Decrypt"
        Resource  = "arn:aws:kms:${local.region}:${data.aws_caller_identity.me.account_id}:key/*"
        Condition = { StringEquals = { "kms:ViaService" = "ssm.${local.region}.amazonaws.com" } }
      },
    ]
  })
}

resource "aws_iam_instance_profile" "box" {
  name = "email-triage-ec2"
  role = aws_iam_role.box.name
}

resource "aws_instance" "box" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = "t4g.small"
  iam_instance_profile   = aws_iam_instance_profile.box.name
  vpc_security_group_ids = [aws_security_group.box.id]
  # Outbound internet without a NAT gateway; the security group still allows no inbound traffic.
  associate_public_ip_address = true
  user_data                   = file("${path.module}/user_data.sh")

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2 # containers sit one network hop behind the host
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 20
    encrypted   = true
  }

  tags = { Name = "email-triage" }

  lifecycle {
    ignore_changes = [ami] # a newer Ubuntu image must not replace the running box
  }
}

resource "aws_ssm_parameter" "secure" {
  for_each = toset(local.secure_params)
  name     = "${local.ssm_prefix}${each.key}"
  type     = "SecureString"
  value    = "CHANGE_ME"
  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "string" {
  for_each = local.string_params
  name     = "${local.ssm_prefix}${each.key}"
  type     = "String"
  value    = each.value
  lifecycle {
    ignore_changes = [value]
  }
}

output "instance_id" {
  value = aws_instance.box.id
}

output "ssm_session_command" {
  value = "aws ssm start-session --region ${local.region} --target ${aws_instance.box.id}"
}
