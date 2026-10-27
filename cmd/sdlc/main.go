package main

import (
	"fmt"
	"os"

	"github.com/blogle/sdlc/internal/cli"
)

func main() {
	if err := cli.New().Execute(); err != nil {
		fmt.Fprintf(os.Stderr, "sdlc: %v\n", err)
		os.Exit(1)
	}
}
