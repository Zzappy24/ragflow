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

package handler

import (
	"net/http"

	"ragflow/internal/service"

	"github.com/gin-gonic/gin"
)

// ListTokens list all API tokens for the active workspace tenant.
// @Summary List API Tokens
// @Description List all API tokens for the active workspace tenant
// @Tags system
// @Accept json
// @Produce json
// @Security ApiKeyAuth
// @Success 200 {object} map[string]interface{}
// @Router /api/v1/system/tokens [get]
func (h *SystemHandler) ListTokens(c *gin.Context) {
	_, errorCode, errorMessage := GetUser(c)
	if errorCode != 0 {
		c.JSON(http.StatusUnauthorized, gin.H{"code": errorCode, "message": errorMessage})
		return
	}
	tenantID := GetTenantID(c)

	tokens, err := h.systemService.ListAPITokens(tenantID)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"code": 500, "message": "Failed to list tokens"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"code": 0, "message": "success", "data": tokens})
}

// CreateToken creates a new API token for the active workspace tenant.
// @Summary Create API Token
// @Description Generate a new API token for the active workspace tenant
// @Tags system
// @Accept json
// @Produce json
// @Security ApiKeyAuth
// @Param name query string false "Name of the token"
// @Success 200 {object} map[string]interface{}
// @Router /api/v1/system/tokens [post]
func (h *SystemHandler) CreateToken(c *gin.Context) {
	_, errorCode, errorMessage := GetUser(c)
	if errorCode != 0 {
		c.JSON(http.StatusUnauthorized, gin.H{"code": errorCode, "message": errorMessage})
		return
	}
	tenantID := GetTenantID(c)

	var req service.CreateAPITokenRequest
	if err := c.ShouldBind(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"code": 400, "message": "Invalid request"})
		return
	}

	token, err := h.systemService.CreateAPIToken(tenantID, &req)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"code": 500, "message": "Failed to create token"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"code": 0, "message": "success", "data": token})
}

// DeleteToken deletes an API token for the active workspace tenant.
// @Summary Delete API Token
// @Description Remove an API token for the active workspace tenant
// @Tags system
// @Accept json
// @Produce json
// @Security ApiKeyAuth
// @Param token path string true "The API token to remove"
// @Success 200 {object} map[string]interface{}
// @Router /api/v1/system/tokens/{token} [delete]
func (h *SystemHandler) DeleteToken(c *gin.Context) {
	_, errorCode, errorMessage := GetUser(c)
	if errorCode != 0 {
		c.JSON(http.StatusUnauthorized, gin.H{"code": errorCode, "message": errorMessage})
		return
	}
	tenantID := GetTenantID(c)

	token := c.Param("token")
	if token == "" {
		c.JSON(http.StatusBadRequest, gin.H{"code": 400, "message": "Token is required"})
		return
	}

	if err := h.systemService.DeleteAPIToken(tenantID, token); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"code": 500, "message": "Failed to delete token"})
		return
	}

	c.JSON(http.StatusOK, gin.H{"code": 0, "message": "success", "data": true})
}
