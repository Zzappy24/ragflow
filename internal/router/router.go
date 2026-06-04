//
//  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
//
//  Licensed under the Apache License, Version 2.0 (the "License");
//  you may not use this file except in compliance with the License.
//  You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
//  Unless required by applicable law or agreed to in writing, software
//  distributed under the License is distributed on an "AS IS" BASIS,
//  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//  See the License for the specific language governing permissions and
//  limitations under the License.
//

package router

import (
	"github.com/gin-gonic/gin"

	"ragflow/internal/common"
	"ragflow/internal/handler"
	"ragflow/internal/middleware"
)

// rbacPerm is a local alias to keep route declarations readable.
// `rbacPerm(common.PermDatasetRead)` ↔ `@require_permission(Permission.DATASET_READ)` in Python.
var rbacPerm = middleware.RequirePermission

type Router struct {
	authHandler          *handler.AuthHandler
	userHandler          *handler.UserHandler
	tenantHandler        *handler.TenantHandler
	documentHandler      *handler.DocumentHandler
	datasetsHandler      *handler.DatasetsHandler
	systemHandler        *handler.SystemHandler
	knowledgebaseHandler *handler.KnowledgebaseHandler
	chunkHandler         *handler.ChunkHandler
	llmHandler           *handler.LLMHandler
	chatHandler          *handler.ChatHandler
	chatSessionHandler   *handler.ChatSessionHandler
	connectorHandler     *handler.ConnectorHandler
	searchHandler        *handler.SearchHandler
	fileHandler          *handler.FileHandler
	memoryHandler        *handler.MemoryHandler
	mcpHandler           *handler.MCPHandler
	skillSearchHandler   *handler.SkillSearchHandler
	providerHandler           *handler.ProviderHandler
	agentHandler              *handler.AgentHandler
	relatedQuestionsHandler   *handler.SearchbotHandler
}

// NewRouter create router
func NewRouter(
	authHandler *handler.AuthHandler,
	userHandler *handler.UserHandler,
	tenantHandler *handler.TenantHandler,
	documentHandler *handler.DocumentHandler,
	datasetsHandler *handler.DatasetsHandler,
	systemHandler *handler.SystemHandler,
	knowledgebaseHandler *handler.KnowledgebaseHandler,
	chunkHandler *handler.ChunkHandler,
	llmHandler *handler.LLMHandler,
	chatHandler *handler.ChatHandler,
	chatSessionHandler *handler.ChatSessionHandler,
	connectorHandler *handler.ConnectorHandler,
	searchHandler *handler.SearchHandler,
	fileHandler *handler.FileHandler,
	memoryHandler *handler.MemoryHandler,
	mcpHandler *handler.MCPHandler,
	skillSearchHandler *handler.SkillSearchHandler,
	providerHandler *handler.ProviderHandler,
	agentHandler *handler.AgentHandler,
	relatedQuestionsHandler *handler.SearchbotHandler,
) *Router {
	return &Router{
		authHandler:          authHandler,
		userHandler:          userHandler,
		tenantHandler:        tenantHandler,
		documentHandler:      documentHandler,
		datasetsHandler:      datasetsHandler,
		systemHandler:        systemHandler,
		knowledgebaseHandler: knowledgebaseHandler,
		chunkHandler:         chunkHandler,
		llmHandler:           llmHandler,
		chatHandler:          chatHandler,
		chatSessionHandler:   chatSessionHandler,
		connectorHandler:     connectorHandler,
		searchHandler:        searchHandler,
		fileHandler:          fileHandler,
		memoryHandler:        memoryHandler,
		mcpHandler:           mcpHandler,
		skillSearchHandler:   skillSearchHandler,
		providerHandler:      providerHandler,
		agentHandler:         agentHandler,
		relatedQuestionsHandler: relatedQuestionsHandler,
	}
}

// Setup setup routes
func (r *Router) Setup(engine *gin.Engine) {
	// Mark all responses from Go with a header for debugging.
	engine.Use(func(c *gin.Context) {
		c.Header("X-API-Source", "go")
		c.Next()
	})

	// Log all HTTP requests.
	engine.Use(gin.Logger())

	// Health check
	engine.GET("/health", r.systemHandler.Health)

	// System endpoints
	engine.GET("/v1/system/configs", r.systemHandler.GetConfigs)
	//engine.POST("/v1/user/register", r.userHandler.Register)

	// User logout endpoint
	engine.GET("/v1/user/logout", r.userHandler.Logout)

	// OAuth callbacks are invoked by third-party providers and cannot rely on
	// the RAGFlow auth middleware.
	engine.GET("/connectors/gmail/oauth/web/callback", r.connectorHandler.GmailWebOAuthCallback)
	engine.GET("/connectors/google-drive/oauth/web/callback", r.connectorHandler.GoogleDriveWebOAuthCallback)

	apiNoAuth := engine.Group("/api/v1")
	{
		apiNoAuth.GET("/system/ping", r.systemHandler.Ping)
		apiNoAuth.GET("/system/config", r.systemHandler.GetConfig)
		apiNoAuth.GET("/system/version", r.systemHandler.GetVersion)
		apiNoAuth.GET("/system/healthz", r.systemHandler.Healthz)

		// User login channels endpoint
		apiNoAuth.GET("/auth/login/channels", r.userHandler.GetLoginChannels)

		// User login by email endpoint
		apiNoAuth.POST("/auth/login", r.userHandler.LoginByEmail)

		// OAuth / OIDC login routes. The static "channels" segment is
		// registered before the wildcard, so gin's tree resolves
		// /auth/login/channels to GetLoginChannels and other values to
		// OAuthLogin without conflict.
		apiNoAuth.GET("/auth/login/:channel", r.userHandler.OAuthLogin)
		apiNoAuth.GET("/auth/oauth/:channel/callback", r.userHandler.OAuthCallback)

		// Register
		apiNoAuth.POST("/users", r.userHandler.Register)

		// Document images are embedded directly in pages and match Python's public route.
		apiNoAuth.GET("/documents/images/:image_id", r.documentHandler.GetDocumentImage)

		// Google redirects here after Gmail / Google Drive web OAuth completes.
		apiNoAuth.GET("/connectors/gmail/oauth/web/callback", r.connectorHandler.GmailWebOAuthCallback)
		apiNoAuth.GET("/connectors/google-drive/oauth/web/callback", r.connectorHandler.GoogleDriveWebOAuthCallback)
	}

	// Protected routes
	authorized := engine.Group("")
	authorized.Use(r.authHandler.AuthMiddleware())
	// Multi-tenant workspace isolation (custom B2B layer — not upstream).
	// Reads X-Workspace-Id, validates membership, injects tenant_id into context.
	// See internal/middleware/workspace.go
	authorized.Use(middleware.NewWorkspaceMiddleware().Resolve())
	{
		// User info endpoint
		authorized.GET("/v1/user/info", r.userHandler.Info)
		// User tenant info endpoint
		authorized.GET("/v1/user/tenant_info", r.tenantHandler.TenantInfo)
		// Tenant list endpoint
		authorized.GET("/v1/tenant/list", r.tenantHandler.TenantList)
		// User settings endpoint
		authorized.POST("/v1/user/setting", r.userHandler.Setting)
		// User change password endpoint
		authorized.POST("/v1/user/setting/password", r.userHandler.ChangePassword)
		// User set tenant info endpoint — writes the workspace default model
		// config. Python decorates it with @require_permission(LLM_CONFIGURE),
		// so only ws_admin (or org_admin / superuser) can change it.
		authorized.POST("/v1/user/set_tenant_info", rbacPerm(common.PermLLMConfigure), r.userHandler.SetTenantInfo)

		// API v1 route group
		v1 := authorized.Group("/api/v1")
		{
			// Auth routes
			auth := v1.Group("/auth")
			{
				// User logout endpoint
				auth.POST("/logout", r.userHandler.Logout)
			}

			// Users routes
			users := v1.Group("/users")
			{
				users.GET("/me", r.userHandler.Info)
				// User settings endpoint
				users.PATCH("/me", r.userHandler.Setting)
				// User tenant info endpoint
				users.GET("/me/models", r.tenantHandler.TenantInfo)
				// User set tenant info endpoint — workspace default model config.
				// Python: @require_permission(LLM_CONFIGURE), ws_admin-only.
				users.PATCH("/me/models", rbacPerm(common.PermLLMConfigure), r.userHandler.SetTenantInfo)
			}

			tenants := v1.Group("/tenants")
			{
				tenants.GET("", r.tenantHandler.TenantList)
				tenants.PATCH("/:tenant_id", r.tenantHandler.AcceptTenantInvite)
				tenants.GET("/:tenant_id/users", r.tenantHandler.ListTenantMembers)
				tenants.POST("/:tenant_id/users", r.tenantHandler.AddTenantMember)
				tenants.DELETE("/:tenant_id/users", r.tenantHandler.RemoveTenantMember)
			}

			v1.GET("/tenant/list", r.tenantHandler.TenantList)

			// Document routes — mirrors @require_permission in restful_apis/document_api.py.
			// Permission convention: upstream uses DOCUMENT_CREATE for both POST and
			// PUT (treating PUT as upsert), DOCUMENT_READ for GET, DOCUMENT_DELETE for
			// DELETE. PARSE writes new chunks, so it's a CREATE-class action.
			documents := v1.Group("/documents")
			{
				documents.POST("", rbacPerm(common.PermDocumentCreate), r.documentHandler.CreateDocument)
				documents.GET("", rbacPerm(common.PermDocumentRead), r.documentHandler.ListDocuments)
				documents.GET("/:id", rbacPerm(common.PermDocumentRead), r.documentHandler.GetDocumentByID)
				documents.PUT("/:id", rbacPerm(common.PermDocumentCreate), r.documentHandler.UpdateDocument)
				documents.DELETE("/:id", rbacPerm(common.PermDocumentDelete), r.documentHandler.DeleteDocument)
				documents.POST("/parse", rbacPerm(common.PermDocumentCreate), r.documentHandler.ParseDocuments)
			}

			// Chat routes — read-only listing; CHAT_CREATE/UPDATE/DELETE live on the
			// legacy /v1/dialog group below.
			chats := v1.Group("/chats")
			{
				chats.GET("", rbacPerm(common.PermChatRead), r.chatHandler.ListChats)
				chats.GET("/:chat_id", rbacPerm(common.PermChatRead), r.chatHandler.GetChat)
				chats.GET("/:chat_id/sessions", rbacPerm(common.PermChatRead), r.chatSessionHandler.ListChatSessions)
			}

			// Searchbot routes
			v1.POST("/searchbots/related_questions", rbacPerm(common.PermChatUse), r.relatedQuestionsHandler.Handle)

			// Dataset routes — mirrors restful_apis/dataset_api.py.
			datasets := v1.Group("/datasets")
			{
				datasets.GET("", rbacPerm(common.PermDatasetRead), r.datasetsHandler.ListDatasets)
				datasets.GET("/:dataset_id", rbacPerm(common.PermDatasetRead), r.datasetsHandler.GetDataset)
				datasets.GET("/:dataset_id/graph", rbacPerm(common.PermDatasetRead), r.datasetsHandler.GetKnowledgeGraph)
				datasets.DELETE("/:dataset_id/tags", rbacPerm(common.PermDatasetUpdate), r.datasetsHandler.RemoveTags)
				datasets.DELETE("/:dataset_id/graph", rbacPerm(common.PermDatasetDelete), r.datasetsHandler.DeleteKnowledgeGraph)
				datasets.POST("", rbacPerm(common.PermDatasetCreate), r.datasetsHandler.CreateDataset)
				datasets.DELETE("", rbacPerm(common.PermDatasetDelete), r.datasetsHandler.DeleteDatasets)
				// /datasets/search is a retrieval-test endpoint — reads only.
				datasets.POST("/search", rbacPerm(common.PermDatasetRead), r.chunkHandler.RetrievalTest)
				datasets.GET("/metadata/flattened", rbacPerm(common.PermDatasetRead), r.datasetsHandler.ListMetadataFlattened)

				// Dataset ingestion logs (read-only).
				datasets.GET("/:dataset_id/ingestions/summary", rbacPerm(common.PermDatasetRead), r.datasetsHandler.GetIngestionSummary)
				datasets.GET("/:dataset_id/ingestions", rbacPerm(common.PermDatasetRead), r.datasetsHandler.ListIngestionLogs)
				datasets.GET("/:dataset_id/ingestions/:log_id", rbacPerm(common.PermDatasetRead), r.datasetsHandler.GetIngestionLog)

				// Metadata Config — GET is read, PUT is an update.
				datasets.GET("/:dataset_id/metadata/config", rbacPerm(common.PermDatasetRead), r.datasetsHandler.GetMetadataConfig)
				datasets.PUT("/:dataset_id/metadata/config", rbacPerm(common.PermDatasetUpdate), r.datasetsHandler.UpdateMetadataConfig)

				// Listing documents within a dataset belongs to the documents scope.
				datasets.GET("/:dataset_id/documents", rbacPerm(common.PermDocumentRead), r.documentHandler.ListDocuments)
				datasets.DELETE("/:dataset_id/documents", rbacPerm(common.PermDocumentDelete), r.documentHandler.DeleteDocuments)

				// Dataset document chunk — single-chunk read + parse + chunk delete.
				datasets.GET("/:dataset_id/documents/:document_id/chunks/:chunk_id", rbacPerm(common.PermDocumentRead), r.chunkHandler.Get)
				datasets.POST("/:dataset_id/documents/parse", rbacPerm(common.PermDocumentCreate), r.documentHandler.ParseDocuments)
				datasets.POST("/:dataset_id/documents/stop", rbacPerm(common.PermDocumentDelete), r.documentHandler.StopParseDocuments)
				datasets.DELETE("/:dataset_id/documents/:document_id/chunks", rbacPerm(common.PermDocumentDelete), r.chunkHandler.RemoveChunks)
			}

			// Search applications — mirrors restful_apis/search_api.py exactly.
			// Note: search "apps" are treated as dataset-scoped configs, hence the
			// DATASET_* permissions (not CHAT_*). Python keeps the same convention.
			searches := v1.Group("/searches")
			{
				searches.GET("", rbacPerm(common.PermDatasetRead), r.searchHandler.ListSearches)
				searches.POST("", rbacPerm(common.PermDatasetRead), r.searchHandler.CreateSearch)
				searches.GET("/:search_id", rbacPerm(common.PermDatasetRead), r.searchHandler.GetSearch)
				searches.PUT("/:search_id", rbacPerm(common.PermDatasetUpdate), r.searchHandler.UpdateSearch)
				searches.DELETE("/:search_id", rbacPerm(common.PermDatasetDelete), r.searchHandler.DeleteSearch)
			}

			// Files — workspace files used as document sources. Permissions
			// mirror the document grants since they back the same content.
			file := v1.Group("/files")
			{
				file.POST("", rbacPerm(common.PermDocumentCreate), r.fileHandler.UploadFile)
				file.GET("", rbacPerm(common.PermDocumentRead), r.fileHandler.ListFiles)
				file.DELETE("", rbacPerm(common.PermDocumentDelete), r.fileHandler.DeleteFiles)
				file.POST("/move", rbacPerm(common.PermDocumentCreate), r.fileHandler.MoveFiles)
				file.GET("/:id/ancestors", rbacPerm(common.PermDocumentRead), r.fileHandler.GetFileAncestors)
				file.GET("/:id/parent", rbacPerm(common.PermDocumentRead), r.fileHandler.GetParentFolder)
				file.GET("/:id", rbacPerm(common.PermDocumentRead), r.fileHandler.Download)
			}

			// Author-scoped document listing.
			authors := v1.Group("/authors")
			{
				authors.GET("/:author_id/documents", rbacPerm(common.PermDocumentRead), r.documentHandler.GetDocumentsByAuthorID)
			}

			// Memory routes — Python uses AGENT_* permissions for memory because
			// memories are configured per-agent in the canvas UI. Keep the same
			// convention so role rules stay consistent across the two backends.
			memory := v1.Group("/memories")
			{
				memory.POST("", rbacPerm(common.PermAgentCreate), r.memoryHandler.CreateMemory)
				memory.PUT("/:memory_id", rbacPerm(common.PermAgentUpdate), r.memoryHandler.UpdateMemory)
				memory.DELETE("/:memory_id", rbacPerm(common.PermAgentDelete), r.memoryHandler.DeleteMemory)
				memory.GET("", rbacPerm(common.PermAgentRead), r.memoryHandler.ListMemories)
				memory.GET("/:memory_id/config", rbacPerm(common.PermAgentRead), r.memoryHandler.GetMemoryConfig)
				memory.GET("/:memory_id", rbacPerm(common.PermAgentRead), r.memoryHandler.GetMemoryMessages)
			}

			// TODO: Message routes - Implementation pending - depends on CanvasService, TaskService and embedding engine
			// message := v1.Group("/messages")
			// {
			// 	message.POST("", r.memoryHandler.AddMessage)
			// 	message.DELETE("/:memory_id/:message_id", r.memoryHandler.ForgetMessage)
			// 	message.PUT("/:memory_id/:message_id", r.memoryHandler.UpdateMessage)
			// 	message.GET("/search", r.memoryHandler.SearchMessage)
			// 	message.GET("", r.memoryHandler.GetMessages)
			// 	message.GET("/:memory_id/:message_id/content", r.memoryHandler.GetMessageContent)
			// }

			// MCP servers — mirrors restful_apis/mcp_api.py which gates every
			// route with @require_permission(Permission.MCP_CONFIGURE).
			mcp := v1.Group("/mcp")
			{
				mcp.POST("/servers", rbacPerm(common.PermMCPConfigure), r.mcpHandler.CreateMCPServer)
				mcp.GET("/servers", rbacPerm(common.PermMCPConfigure), r.mcpHandler.ListMCPServers)
				mcp.PUT("/servers/:mcp_id", rbacPerm(common.PermMCPConfigure), r.mcpHandler.UpdateMCPServer)
				mcp.DELETE("/servers/:mcp_id", rbacPerm(common.PermMCPConfigure), r.mcpHandler.DeleteMCPServer)
			}

			// Skills — Go-only feature (no Python equivalent). The whole subtree
			// is gated by DATASET_* permissions because a skill space behaves as
			// a dataset for the user (it indexes content and feeds retrieval).
			skills := v1.Group("/skills")
			{
				skills.GET("/spaces", rbacPerm(common.PermDatasetRead), r.skillSearchHandler.ListSpaces)
				skills.POST("/spaces", rbacPerm(common.PermDatasetCreate), r.skillSearchHandler.CreateSpace)
				skills.GET("/spaces/:space_id", rbacPerm(common.PermDatasetRead), r.skillSearchHandler.GetSpace)
				skills.PUT("/spaces/:space_id", rbacPerm(common.PermDatasetUpdate), r.skillSearchHandler.UpdateSpace)
				skills.DELETE("/spaces/:space_id", rbacPerm(common.PermDatasetDelete), r.skillSearchHandler.DeleteSpace)
				skills.GET("/space/by-folder", rbacPerm(common.PermDatasetRead), r.skillSearchHandler.GetSpaceByFolder)

				skills.GET("/config", rbacPerm(common.PermDatasetRead), r.skillSearchHandler.GetConfig)
				skills.POST("/config", rbacPerm(common.PermDatasourceConfigure), r.skillSearchHandler.UpdateConfig)

				skills.POST("/search", rbacPerm(common.PermDatasetRead), r.skillSearchHandler.Search)
				skills.POST("/index", rbacPerm(common.PermDatasetUpdate), r.skillSearchHandler.IndexSkills)
				skills.DELETE("/index", rbacPerm(common.PermDatasetDelete), r.skillSearchHandler.DeleteSkillIndex)
				skills.POST("/reindex", rbacPerm(common.PermDatasetUpdate), r.skillSearchHandler.Reindex)
			}

			// Provider pool — LLM provider management. ALL writes are LLM_CONFIGURE
			// (ws_admin only). Reads are gated by CHAT_USE so any user with chat
			// access can see the model list (needed for the model picker UI).
			//
			// NOTE for B2B SaaS: we already manage providers per-workspace via
			// Python `tenant_model_service`. The Go provider routes target the
			// global ragflow provider pool — gating them at LLM_CONFIGURE keeps
			// the upstream feature usable without surprising our admin UX.
			provider := v1.Group("/providers")
			{
				provider.GET("/", rbacPerm(common.PermChatUse), r.providerHandler.ListProviders)
				provider.PUT("/", rbacPerm(common.PermLLMConfigure), r.providerHandler.AddProvider)
				provider.GET("/:provider_name", rbacPerm(common.PermChatUse), r.providerHandler.ShowProvider)
				provider.DELETE("/:provider_name", rbacPerm(common.PermLLMConfigure), r.providerHandler.DeleteProvider)
				provider.GET("/:provider_name/models", rbacPerm(common.PermChatUse), r.providerHandler.ListModels)
				provider.GET("/:provider_name/models/:model_name", rbacPerm(common.PermChatUse), r.providerHandler.ShowModel)
				provider.POST("/:provider_name/instances", rbacPerm(common.PermLLMConfigure), r.providerHandler.CreateProviderInstance)
				provider.GET("/:provider_name/instances", rbacPerm(common.PermChatUse), r.providerHandler.ListProviderInstances)
				provider.GET("/:provider_name/instances/:instance_name", rbacPerm(common.PermChatUse), r.providerHandler.ShowProviderInstance)
				provider.GET("/:provider_name/instances/:instance_name/balance", rbacPerm(common.PermLLMConfigure), r.providerHandler.ShowInstanceBalance)
				provider.GET("/:provider_name/instances/:instance_name/connection", rbacPerm(common.PermLLMConfigure), r.providerHandler.CheckInstanceConnection)
				provider.GET("/:provider_name/connection", rbacPerm(common.PermLLMConfigure), r.providerHandler.CheckConnection)
				provider.GET("/:provider_name/instances/:instance_name/tasks", rbacPerm(common.PermLLMConfigure), r.providerHandler.ListTasks)
				provider.GET("/:provider_name/instances/:instance_name/tasks/:task_id", rbacPerm(common.PermLLMConfigure), r.providerHandler.ShowTask)
				provider.PUT("/:provider_name/instances/:instance_name", rbacPerm(common.PermLLMConfigure), r.providerHandler.AlterProviderInstance)
				provider.DELETE("/:provider_name/instances", rbacPerm(common.PermLLMConfigure), r.providerHandler.DropProviderInstance)
				provider.GET("/:provider_name/instances/:instance_name/models", rbacPerm(common.PermChatUse), r.providerHandler.ListInstanceModels)
				provider.PATCH("/:provider_name/instances/:instance_name/models/*model_name", rbacPerm(common.PermLLMConfigure), r.providerHandler.EnableOrDisableModel)
				// upstream 2026-06-04 renamed AddCustomModel → AddModel.
				provider.POST("/:provider_name/instances/:instance_name/models", rbacPerm(common.PermLLMConfigure), r.providerHandler.AddModel)
				provider.DELETE("/:provider_name/instances/:instance_name/models", rbacPerm(common.PermLLMConfigure), r.providerHandler.DropInstanceModels)

				// OpenAI-compatible inference endpoints — anyone who can use chat
				// can call these. Cost/billing is controlled at the workspace
				// level by `tenant_model_service`.
				v1.POST("/chat/completions", rbacPerm(common.PermChatUse), r.providerHandler.ChatToModel)
				v1.POST("/embeddings", rbacPerm(common.PermChatUse), r.providerHandler.EmbedText)
				v1.POST("/rerank", rbacPerm(common.PermChatUse), r.providerHandler.RerankDocument)
				v1.POST("/audio/transcriptions", rbacPerm(common.PermChatUse), r.providerHandler.TranscribeAudio)
				v1.POST("/audio/speech", rbacPerm(common.PermChatUse), r.providerHandler.AudioSpeech)
				v1.POST("/file/ocr", rbacPerm(common.PermChatUse), r.providerHandler.OCRFile)
				v1.POST("/file/parse", rbacPerm(common.PermChatUse), r.providerHandler.ParseFile)
			}

			// Per-tenant default model config. Reading needs no special perm
			// (the chat picker needs it); writing is LLM_CONFIGURE (ws_admin).
			model := v1.Group("/models")
			{
				model.GET("/", rbacPerm(common.PermChatUse), r.tenantHandler.GetModels)
				model.PATCH("/", rbacPerm(common.PermLLMConfigure), r.tenantHandler.SetModels)
			}

			// Agent routes — mirror Python's agent_api.py (PermAgentRead for read).
			agents := v1.Group("/agents")
			{
				agents.GET("", rbacPerm(common.PermAgentRead), r.agentHandler.ListAgents)
				agents.GET("/:agent_id/versions", rbacPerm(common.PermAgentRead), r.agentHandler.ListAgentVersions)
				agents.GET("/:agent_id/versions/:version_id", rbacPerm(common.PermAgentRead), r.agentHandler.GetAgentVersion)
				agents.POST("/:agent_id/upload", rbacPerm(common.PermAgentUpdate), r.agentHandler.UploadAgentFile)
			}

			// Connectors — see legacy /v1/connector above for the rationale
			// (DATASOURCE_CONFIGURE = ws_admin only because credentials live here).
			connector := v1.Group("/connectors")
			{
				connector.GET("/", rbacPerm(common.PermDatasourceConfigure), r.connectorHandler.ListConnectors)
				connector.POST("/", rbacPerm(common.PermDatasourceConfigure), r.connectorHandler.CreateConnector)
				connector.POST("/google/oauth/web/start", rbacPerm(common.PermDatasourceConfigure), r.connectorHandler.StartGoogleWebOAuth)
				connector.POST("/google/oauth/web/result", rbacPerm(common.PermDatasourceConfigure), r.connectorHandler.PollGoogleWebOAuthResult)
				connector.GET("/:connector_id", rbacPerm(common.PermDatasourceConfigure), r.connectorHandler.GetConnector)
				connector.GET("/:connector_id/logs", rbacPerm(common.PermDatasourceConfigure), r.connectorHandler.ListLogs)
				connector.DELETE("/:connector_id", rbacPerm(common.PermDatasourceConfigure), r.connectorHandler.DeleteConnector)
				connector.POST("/:connector_id/rebuild", rbacPerm(common.PermDatasourceConfigure), r.connectorHandler.RebuildConnector)
				connector.POST("/:connector_id/test", rbacPerm(common.PermDatasourceConfigure), r.connectorHandler.TestConnector)
			}

			// System config / log / tokens.
			//   configs (read)  → no special perm beyond auth; visible to ws_admin via UI but harmless to surface.
			//   log GET         → AUDIT_READ (operational diagnostics, sensitive paths).
			//   log PUT         → LLM_CONFIGURE (admin-only changing log levels is rare and impactful).
			//   tokens.*        → API_KEY_MANAGE (managing service tokens for the tenant).
			system := v1.Group("/system")
			{
				system.GET("/configs", rbacPerm(common.PermChatUse), r.systemHandler.GetConfigs)
				// /status + /stats — open to any authenticated user (mirrors Python's
				// system_api.py which leaves these without @require_permission).
				system.GET("/status", r.systemHandler.GetStatus)
				system.GET("/stats", r.systemHandler.GetStats)
				// Legacy /system/log group — keep the RBAC gating (AUDIT_READ for read,
				// LLM_CONFIGURE for write). Upstream moved to /system/config/log; we
				// mirror that too so frontend clients pinned on either path resolve.
				log := system.Group("/log")
				{
					log.GET("", rbacPerm(common.PermAuditRead), r.systemHandler.GetLogLevel)
					log.PUT("", rbacPerm(common.PermLLMConfigure), r.systemHandler.SetLogLevel)
				}
				config := system.Group("/config")
				{
					config.GET("/log", rbacPerm(common.PermAuditRead), r.systemHandler.GetLogLevel)
					config.PUT("/log", rbacPerm(common.PermLLMConfigure), r.systemHandler.SetLogLevel)
				}

				//log := system.Group("/log")
				//{
				//	// /api/v1/system/log GET
				//	log.GET("", r.systemHandler.GetLogLevel)
				//	// /api/v1/system/log PUT
				//	log.PUT("", r.systemHandler.SetLogLevel)
				//}

				tokens := system.Group("/tokens")
				{
					tokens.GET("", rbacPerm(common.PermAPIKeyManage), r.systemHandler.ListTokens)
					tokens.POST("", rbacPerm(common.PermAPIKeyManage), r.systemHandler.CreateToken)
					tokens.DELETE("/:token", rbacPerm(common.PermAPIKeyManage), r.systemHandler.DeleteToken)
				}
			}
		}

		// ---------------------------------------------------------------
		// Legacy /v1/* groups — same handlers, kept for backward compat
		// with the Python web-app frontend (web/src/utils/api.ts pins
		// `restAPIv1 = /v1`). Permissions mirror the new /api/v1/* routes.
		// ---------------------------------------------------------------

		// Knowledge base — datasets viewed through the legacy POST-everything API.
		kb := authorized.Group("/v1/kb")
		{
			kb.POST("/list", rbacPerm(common.PermDatasetRead), r.knowledgebaseHandler.ListKbs)
			kb.POST("/rm", rbacPerm(common.PermDatasetDelete), r.knowledgebaseHandler.DeleteKB)
			kb.POST("/update", rbacPerm(common.PermDatasetUpdate), r.knowledgebaseHandler.UpdateKB)
			kb.POST("/update_metadata_setting", rbacPerm(common.PermDatasetUpdate), r.knowledgebaseHandler.UpdateMetadataSetting)
			kb.GET("/detail", rbacPerm(common.PermDatasetRead), r.knowledgebaseHandler.GetDetail)
			kb.GET("/tags", rbacPerm(common.PermDatasetRead), r.knowledgebaseHandler.ListTagsFromKbs)
			kb.GET("/get_meta", rbacPerm(common.PermDatasetRead), r.knowledgebaseHandler.GetMeta)
			kb.GET("/basic_info", rbacPerm(common.PermDatasetRead), r.knowledgebaseHandler.GetBasicInfo)
			// upstream 2026-06-04 removed knowledgebaseHandler.CreateDatasetInDocEngine,
			// DeleteDatasetInDocEngine, InsertDatasetFromFile and RemoveTags — their
			// underlying Go internals were refactored. The corresponding Go routes
			// (kb.POST/doc_engine_table, kb.DELETE/doc_engine_table,
			// kb.POST/insert_from_file, kbByID.POST/rm_tags) are dropped here.
			// The Python `/v1/kb` and `/v1/llm/factories` endpoints still serve the
			// same surface, and nothing in our frontend/sdk/test references the Go
			// variants directly, so this is a safe removal.

			// KB ID specific routes
			kbByID := kb.Group("/:kb_id")
			{
				kbByID.GET("/tags", rbacPerm(common.PermDatasetRead), r.knowledgebaseHandler.ListTags)
				kbByID.POST("/rename_tag", rbacPerm(common.PermDatasetUpdate), r.knowledgebaseHandler.RenameTag)
				kbByID.GET("/knowledge_graph", rbacPerm(common.PermDatasetRead), r.knowledgebaseHandler.KnowledgeGraph)
				kbByID.DELETE("/knowledge_graph", rbacPerm(common.PermDatasetDelete), r.knowledgebaseHandler.DeleteKnowledgeGraph)
			}
		}

		// Tenant — Go-only internal doc-engine metadata helpers. Treated as
		// datasource configuration (ws_admin only via the matrix).
		tenant := authorized.Group("/v1/tenant")
		{
			tenant.POST("/doc_engine_metadata_table", rbacPerm(common.PermDatasourceConfigure), r.tenantHandler.CreateMetadataInDocEngine)
			tenant.DELETE("/doc_engine_metadata_table", rbacPerm(common.PermDatasourceConfigure), r.tenantHandler.DeleteMetadataInDocEngine)
			tenant.POST("/insert_metadata_from_file", rbacPerm(common.PermDocumentCreate), r.tenantHandler.InsertMetadataFromFile)
		}

		// New /api/v1/tenant — Go-internal upstream additions, all gated by
		// DATASOURCE_CONFIGURE (chunk/metadata stores rewrite doc-engine layouts).
		tenantV1 := v1.Group("/tenant")
		{
			tenantV1.POST("/chunk_store", rbacPerm(common.PermDatasourceConfigure), r.tenantHandler.CreateChunkStore)
			tenantV1.DELETE("/chunk_store", rbacPerm(common.PermDatasourceConfigure), r.tenantHandler.DeleteChunkStore)
			tenantV1.POST("/metadata_store", rbacPerm(common.PermDatasourceConfigure), r.tenantHandler.CreateMetadataStore)
			tenantV1.DELETE("/metadata_store", rbacPerm(common.PermDatasourceConfigure), r.tenantHandler.DeleteMetadataStore)
			tenantV1.POST("/insert_chunks_from_file", rbacPerm(common.PermDocumentCreate), r.tenantHandler.InsertChunksFromFile)
			tenantV1.POST("/insert_metadata_from_file", rbacPerm(common.PermDocumentCreate), r.tenantHandler.InsertMetadataFromFile)
		}

		// Document — legacy listing and metadata operations.
		doc := authorized.Group("/v1/document")
		{
			doc.POST("/list", rbacPerm(common.PermDocumentRead), r.documentHandler.ListDocuments)
			doc.POST("/metadata/summary", rbacPerm(common.PermDocumentRead), r.documentHandler.MetadataSummary)
			doc.POST("/set_meta", rbacPerm(common.PermDocumentCreate), r.documentHandler.SetMeta)
		}

		// New /api/v1/document RESTful surface — adds delete_meta vs legacy.
		docV1 := v1.Group("/document")
		{
			docV1.POST("/list", rbacPerm(common.PermDocumentRead), r.documentHandler.ListDocuments)
			docV1.POST("/metadata/summary", rbacPerm(common.PermDocumentRead), r.documentHandler.MetadataSummary)
			docV1.POST("/set_meta", rbacPerm(common.PermDocumentCreate), r.documentHandler.SetMeta)
			docV1.POST("/delete_meta", rbacPerm(common.PermDocumentDelete), r.documentHandler.DeleteMeta)
		}

		v1.GET("/thumbnails", rbacPerm(common.PermDocumentRead), r.documentHandler.GetThumbnail)

		// Chunk — retrieval test reads dataset; update/rm are document-level edits.
		// The Internal Go-only /update is gated by ws_admin via DATASOURCE_CONFIGURE
		// because it writes directly into the doc engine bypassing normal ingestion.
		chunk := authorized.Group("/v1/chunk")
		{
			chunk.POST("/retrieval_test", rbacPerm(common.PermDatasetRead), r.chunkHandler.RetrievalTest)
			chunk.GET("/get", rbacPerm(common.PermDocumentRead), r.chunkHandler.Get)
			chunk.POST("/list", rbacPerm(common.PermDocumentRead), r.chunkHandler.List)
			chunk.POST("/update", rbacPerm(common.PermDatasourceConfigure), r.chunkHandler.UpdateChunk)
			chunk.POST("/rm", rbacPerm(common.PermDocumentDelete), r.chunkHandler.Remove)
		}

		// New /api/v1/chunk RESTful surface for the Python list + Go-internal update.
		chunkV1 := v1.Group("/chunk")
		{
			chunkV1.POST("/list", rbacPerm(common.PermDocumentRead), r.chunkHandler.List)
			chunkV1.POST("/update", rbacPerm(common.PermDatasourceConfigure), r.chunkHandler.UpdateChunk)
		}

		// LLM — Python LLM_CONFIGURE is ws_admin-only. Reads (my_llms, factories,
		// list) are visible to anyone with chat usage so the dropdown works.
		llm := authorized.Group("/v1/llm")
		{
			llm.GET("/my_llms", rbacPerm(common.PermChatUse), r.llmHandler.GetMyLLMs)
			// upstream 2026-06-04 removed llmHandler.Factories; Python /v1/llm/factories
			// in api/apps/llm_app.py still serves the same payload.
			llm.GET("/list", rbacPerm(common.PermChatUse), r.llmHandler.ListApp)
			llm.POST("/set_api_key", rbacPerm(common.PermLLMConfigure), r.llmHandler.SetAPIKey)
		}

		// Chat (dialog) — Python decorators: chat_api uses CHAT_READ/UPDATE/DELETE.
		// `next` is the paginated list (READ), `set` is upsert (UPDATE), `rm` deletes.
		chat := authorized.Group("/v1/dialog")
		{
			chat.POST("/next", rbacPerm(common.PermChatRead), r.chatHandler.ListChatsNext)
			chat.POST("/set", rbacPerm(common.PermChatUpdate), r.chatHandler.SetDialog)
			chat.POST("/rm", rbacPerm(common.PermChatDelete), r.chatHandler.RemoveChats)
		}

		// Conversation (chat session) — CHAT_USE for completion (running the chat),
		// CHAT_READ for listing, CHAT_UPDATE/DELETE for state changes.
		session := authorized.Group("/v1/conversation")
		{
			session.POST("/set", rbacPerm(common.PermChatUpdate), r.chatSessionHandler.SetChatSession)
			session.POST("/rm", rbacPerm(common.PermChatDelete), r.chatSessionHandler.RemoveChatSessions)
			session.GET("/list", rbacPerm(common.PermChatRead), r.chatSessionHandler.ListChatSessions)
			session.POST("/completion", rbacPerm(common.PermChatUse), r.chatSessionHandler.Completion)
		}

		// Connector — listing data sources is part of the workspace data view.
		// Treated as DATASOURCE_CONFIGURE because connectors are sensitive
		// (contain credentials); even read access is restricted to ws_admin.
		connector := authorized.Group("/v1/connector")
		{
			connector.GET("/list", rbacPerm(common.PermDatasourceConfigure), r.connectorHandler.ListConnectors)
			connector.GET("/:connector_id", rbacPerm(common.PermDatasourceConfigure), r.connectorHandler.GetConnector)
			connector.POST("/:connector_id/rebuild", rbacPerm(common.PermDatasourceConfigure), r.connectorHandler.RebuildConnector)
		}

		// File folder lookups — pure read access on the workspace file tree.
		file := authorized.Group("/v1/file")
		{
			file.GET("/root_folder", rbacPerm(common.PermDocumentRead), r.fileHandler.GetRootFolder)
			file.GET("/parent_folder", rbacPerm(common.PermDocumentRead), r.fileHandler.GetParentFolder)
			file.GET("/all_parent_folder", rbacPerm(common.PermDocumentRead), r.fileHandler.GetAllParentFolders)
		}

	}

	// Handle undefined routes
	engine.NoRoute(handler.HandleNoRoute)
}
