package internal

// Copyright 2023, DASH-Industry Forum. All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE.md file.

import (
	"compress/gzip"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"log/slog"
	"os"
	"path"

	"github.com/wwmoraes/go-rwfs"
)

const (
	MPDListFile = "mpdlist.json"
)

// MPDData stores mpd name to original URI relation.
type MPDData struct {
	Name    string `json:"name"`
	OrigURI string `json:"originURI"`
	Title   string `json:"titleStr"`
	Dur     string `json:"durStr"` // Dur is MediaPresentationDuration
	MPDStr  string `json:"mpdStr"`
}

// WriteMPDData to file on disk.
func WriteMPDData(dirPath string, name, uri string) error {
	filePath := path.Join(dirPath, MPDListFile)
	_, err := os.Stat(filePath)
	exists := !os.IsNotExist(err)
	var mpds []MPDData
	if exists {
		data, err := os.ReadFile(filePath)
		if err != nil {
			return err
		}
		err = json.Unmarshal(data, &mpds)
		if err != nil {
			return err
		}
	}
	mpds = append(mpds, MPDData{Name: name, OrigURI: uri})
	outData, err := json.MarshalIndent(mpds, "", "  ")
	if err != nil {
		return err
	}
	err = os.WriteFile(filePath, outData, 0644)
	if err != nil {
		return err
	}
	return nil
}

// ReadMPDData for MPD from file on disk.
func ReadMPDData(vodFS fs.FS, mpdPath string) MPDData {
	assetPath, mpdName := path.Split(mpdPath)
	if assetPath != "" {
		assetPath = assetPath[:len(assetPath)-1]
	}
	md := MPDData{Name: mpdName}

	mpdData, err := fs.ReadFile(vodFS, mpdPath)
	if err != nil {
		return md
	}
	md.MPDStr = string(mpdData)

	mpdListPath := path.Join(assetPath, MPDListFile)
	data, err := fs.ReadFile(vodFS, mpdListPath)
	if err != nil {
		return md
	}
	var mds []MPDData
	err = json.Unmarshal(data, &mds)
	if err != nil {
		return md
	}
	for _, m := range mds {
		if m.Name == mpdName {
			m.MPDStr = md.MPDStr
			return m
		}
	}
	return md
}

func (md *MPDData) LoadFromJSON(logger *slog.Logger, repFS fs.FS, dataPath string) (bool, error) {
	if repFS == nil {
		return false, nil
	}
	mpdDir, mpdFile := path.Split(dataPath)
	mpdDataPath := path.Join(mpdDir, mpdFile+"_data.json")
	gzipPath := mpdDataPath + ".gz"
	var data []byte
	fh, err := repFS.Open(gzipPath)
	if err == nil {
		defer fh.Close()
		gzr, err := gzip.NewReader(fh)
		if err != nil {
			return true, err
		}
		defer gzr.Close()
		data, err = io.ReadAll(gzr)
		if err != nil {
			return true, err
		}
		logger.Info("Read gzipped mpdData", "path", gzipPath)
	}
	if len(data) == 0 {
		return false, nil
	}
	if err := json.Unmarshal(data, md); err != nil {
		return true, err
	}
	return true, nil
}

func (md *MPDData) WriteToJSON(logger *slog.Logger, repFS rwfs.FS, dataPath string) error {
	data, err := json.Marshal(md)
	if err != nil {
		return err
	}
	mpdDir, mpdFile := path.Split(dataPath)
	err = repFS.MkdirAll(mpdDir, 0755)
	if err != nil {
		return fmt.Errorf("mkdir: %w", err)
	}
	mpdDataPath := path.Join(mpdDir, mpdFile+"_data.json")
	gzipPath := mpdDataPath + ".gz"
	fh, err := repFS.OpenFile(gzipPath, os.O_RDWR|os.O_CREATE|os.O_TRUNC, 0666)
	if err != nil {
		return err
	}
	defer fh.Close()
	gzw := gzip.NewWriter(fh)
	defer gzw.Close()
	_, err = gzw.Write(data)
	if err != nil {
		return err
	}
	logger.Info("Wrote mpdData", "path", gzipPath)
	return nil
}
