variable "github_oidc_provider_exists" {
  type        = bool
  description = "Whether this account already has an IAM provider for token.actions.githubusercontent.com."
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.github_oidc_provider_exists ? 1 : 0
  url   = "https://token.actions.githubusercontent.com"

  lifecycle {
    postcondition {
      condition     = contains(self.client_id_list, "sts.amazonaws.com")
      error_message = "The existing GitHub OIDC provider must include the sts.amazonaws.com audience."
    }
  }
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.github_oidc_provider_exists ? 0 : 1
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

resource "aws_ssm_document" "backend_deploy" {
  name            = "EmailTriageDeployBackend"
  document_type   = "Command"
  document_format = "YAML"
  content         = file("${path.module}/backend_deploy.yml")
}

resource "aws_iam_role" "github_backend_deploy" {
  name = "email-triage-github-backend-deploy"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRoleWithWebIdentity"
      Principal = {
        Federated = var.github_oidc_provider_exists ? data.aws_iam_openid_connect_provider.github[0].arn : aws_iam_openid_connect_provider.github[0].arn
      }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:prakhargaming@88959146/email-summarizer@1459494677:ref:refs/heads/main"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_backend_deploy" {
  name = "deploy-email-triage-with-ssm"
  role = aws_iam_role.github_backend_deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "ssm:SendCommand"
        Resource = [aws_ssm_document.backend_deploy.arn, aws_instance.box.arn]
      },
      {
        # GetCommandInvocation has no resource-level IAM permissions.
        Effect    = "Allow"
        Action    = "ssm:GetCommandInvocation"
        Resource  = "*"
        Condition = { StringEquals = { "aws:RequestedRegion" = local.region } }
      },
    ]
  })
}

output "github_backend_deploy_role_arn" {
  value = aws_iam_role.github_backend_deploy.arn
}
