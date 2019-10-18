HOSTNAME:=$(shell hostname)
PWD:=$(shell pwd -LP)

packages:
	@echo -e "- installing packages"
	sudo pacman -S --needed - < $(PWD)/packages.txt

links:
	@echo -e "- creating config folder"
	mkdir -p ~/.config

	@echo -e "- creating symlinks"
	ln -sf $(PWD)/xinitrc      ~/.xinitrc
	ln -sf $(PWD)/Xmodmap      ~/.Xmodmap
	ln -sf $(PWD)/Xresources   ~/.Xresources
	ln -sf $(PWD)/gtkrc-2.0    ~/.gtkrc-2.0
	ln -sf $(PWD)/i3           ~/.config/i3
	ln -sf $(PWD)/i3blocks     ~/.config/i3blocks
	ln -sf $(PWD)/redshift     ~/.config/redshift
	ln -sf $(PWD)/compton.conf ~/.config/compton.conf