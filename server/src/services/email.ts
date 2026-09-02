import nodemailer from "nodemailer";

const transporter = nodemailer.createTransport({
  host: process.env.SMTP_HOST,
  port: Number(process.env.SMTP_PORT),
  secure: false,
  auth: { user: process.env.SMTP_USER, pass: process.env.SMTP_PASS },
});

export async function sendOtpEmail(
  toEmail: string,
  code: string,
): Promise<void> {
  await transporter.sendMail({
    from: `"Clueso Docs" <${process.env.SMTP_USER}>`,
    to: toEmail,
    subject: "Your sign-in code",
    text: `Your verification code is ${code}. It expires in ${process.env.OTP_EXPIRY_MINUTES} minutes.`,
    html: `<p>Your verification code is:</p><h2 style="letter-spacing:4px">${code}</h2><p>This code expires in ${process.env.OTP_EXPIRY_MINUTES} minutes.</p>`,
  });
}
