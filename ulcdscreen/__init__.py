lcd_i2c = __import__("micropython-i2c-lcd.lcd_i2c")


class LcdScreen:
    lcd_driver = None
    i2c_address = None
    columns = 16
    rows = 2
    _CUSTOM_CHARS = [(0, list([0x04, 0x0E, 0x0E, 0x0E, 0x1F, 0x11, 0x04, 0x0E])),]

    def __init__(self, i2c, i2c_address=None, columns=16, rows=2):
        self.columns = columns
        self.rows = rows
        self.i2c_address = i2c_address

        self.lcd_driver = lcd_i2c.lcd_i2c.LCD(
            addr=self.i2c_address, cols=self.columns, rows=self.rows, i2c=i2c
        ) if i2c_address else None

        if self.lcd_driver:
            self.lcd_driver.begin()

            for character in self._CUSTOM_CHARS:
                self.lcd_driver.create_char(character[0], character[1])

                # for some reason you need to print a custom character at least once before it actually renders
                self.lcd_driver.print(chr(character[0]))
                self.clear()

    def print(self, text):
        if self.lcd_driver:
            self.lcd_driver.print(text)

    def print_rocket(self):
        self.print(chr(0))

    def clear(self):
        if self.lcd_driver:
            self.lcd_driver.clear()
