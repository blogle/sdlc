package nix

import (
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"strings"
)

var systemName = regexp.MustCompile(`^[A-Za-z0-9_-]+$`)

func InvokeConsumerPublish(flake, system string) error {
	if !systemName.MatchString(system) {
		return fmt.Errorf("consumer system must be a Nix system identifier")
	}
	installable := flake + "#ci." + system + ".publish.program"
	program, err := evaluate("--no-write-lock-file", "--raw", installable)
	if err != nil {
		return err
	}
	program = strings.TrimSpace(program)
	derivation, err := evaluate("--no-write-lock-file", "--raw", installable, "--apply", "program: builtins.head (builtins.attrNames (builtins.getContext program))")
	if err != nil {
		return err
	}
	derivation = strings.TrimSpace(derivation)
	storeDir, err := evaluate("--no-write-lock-file", "--raw", "--expr", "builtins.storeDir")
	if err != nil {
		return err
	}
	storeDir = strings.TrimSpace(storeDir)
	if !strings.HasPrefix(program, storeDir+"/") || !strings.HasSuffix(derivation, ".drv") {
		return fmt.Errorf("ci.publish.program is not a store-backed executable Nix app")
	}
	if err := run("nix-store", "--realise", derivation); err != nil {
		return err
	}
	info, err := os.Stat(program)
	if err != nil || info.Mode()&0o111 == 0 {
		return fmt.Errorf("ci.publish.program did not build an executable")
	}
	return run(program)
}

func UpdateSDLC() error {
	if err := run("nix", "flake", "update", "sdlc"); err != nil {
		return err
	}
	if err := run("nix", "run", ".#sdlc", "--", "install"); err != nil {
		return err
	}
	return run("nix", "run", ".#sdlc", "--", "install", "--check")
}

func evaluate(args ...string) (string, error) {
	return output("nix", append([]string{"eval"}, args...)...)
}

func output(name string, args ...string) (string, error) {
	result, err := exec.Command(name, args...).Output()
	if err != nil {
		return "", err
	}
	return string(result), nil
}

func run(name string, args ...string) error {
	command := exec.Command(name, args...)
	command.Stdin, command.Stdout, command.Stderr = os.Stdin, os.Stdout, os.Stderr
	return command.Run()
}
