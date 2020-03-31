HOSTNAME:=$(shell hostname)
PWD:=$(shell pwd -LP)

pkg:
	@echo -e "- installing packages"
	sudo pacman -S --needed - < $(PWD)/packages.txt

link:
	@echo -e "- creating config folder"
	mkdir -p ~/.config
	@echo -e "- creating symlinks"
	ln -nsf $(PWD)/xinitrc             ~/.xinitrc
	ln -nsf $(PWD)/Xmodmap             ~/.Xmodmap
	ln -nsf $(PWD)/Xresources          ~/.Xresources
	ln -nsf $(PWD)/gtkrc-2.0           ~/.gtkrc-2.0
	ln -nsf $(PWD)/i3                  ~/.config/i3
	ln -nsf $(PWD)/polybar             ~/.config/polybar
	ln -nsf $(PWD)/chromium-flags.conf ~/.config/chromium-flags.conf
	