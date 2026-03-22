package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type User struct {
	Name              string `json:"name"`
	SubscriptionToken string `json:"subscription_token"`
	Enabled           *bool  `json:"enabled,omitempty"`
	Upload            int64  `json:"upload,omitempty"`
	Download          int64  `json:"download,omitempty"`
	TrafficLimit      int64  `json:"traffic_limit,omitempty"`
	ExpireAt          string `json:"expire_at,omitempty"`
}

var baseDir string
var userDir string
var usersFile string

func init() {
	var err error
	baseDir, err = filepath.Abs(".")
	if err != nil {
		panic(err)
	}
	userDir = filepath.Join(baseDir, "singbox", "client", "users")
	usersFile = filepath.Join(baseDir, "singbox", "users.json")
}

// --- 用户管理 ---
func loadUsers() ([]User, error) {
	if _, err := os.Stat(usersFile); os.IsNotExist(err) {
		return []User{}, nil
	}
	data, err := os.ReadFile(usersFile)
	if err != nil {
		return nil, err
	}
	var users []User
	if err := json.Unmarshal(data, &users); err != nil {
		return nil, err
	}
	return users, nil
}

func getUserByToken(token string) *User {
	users, err := loadUsers()
	if err != nil {
		return nil
	}
	for _, u := range users {
		if u.SubscriptionToken == token {
			return &u
		}
	}
	return nil
}

// --- 下载订阅 ---
func downloadByTokenHandler(w http.ResponseWriter, r *http.Request) {
	parts := strings.Split(r.URL.Path, "/")
	if len(parts) < 3 {
		http.Error(w, "missing token", 400)
		return
	}
	token := parts[2]
	user := getUserByToken(token)
	if user == nil || (user.Enabled != nil && !*user.Enabled) {
		http.Error(w, "user not found or disabled", 403)
		return
	}

	manageFile := filepath.Join(userDir, fmt.Sprintf("%s.json", user.Name))
	absPath, _ := filepath.Abs(manageFile)
	absBase, _ := filepath.Abs(userDir)
	if !strings.HasPrefix(absPath, absBase) || !fileExists(absPath) {
		http.Error(w, "config not found", 404)
		return
	}

	content, err := os.ReadFile(absPath)
	if err != nil {
		http.Error(w, "read error", 500)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Content-Disposition", "inline")
	expireTs := int64(0)
	if user.ExpireAt != "" {
		if t, err := time.Parse(time.RFC3339, user.ExpireAt); err == nil {
			expireTs = t.Unix()
		}
	}
	w.Header().Set("subscription-userinfo",
		fmt.Sprintf("upload=%d; download=%d; total=%d; expire=%d",
			user.Upload, user.Download, user.TrafficLimit, expireTs))
	w.Write(content)
}

// --- 辅助 ---
func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

// --- 主函数 ---
func main() {
	http.HandleFunc("/sub/", downloadByTokenHandler)

	fmt.Println("Go subscription server running on :5000")
	if err := http.ListenAndServe(":5000", nil); err != nil {
		panic(err)
	}
}
